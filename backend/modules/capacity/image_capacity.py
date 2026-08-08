"""
Preset-aware image capacity calculator for the block-based DCT-QIM engine.

``image_capacity`` returns, for EVERY image preset in one call, the maximum
payload bytes a cover image can carry for each allowed payload type, computed
for a given channel-level :class:`CompressionPreset`. The model mirrors the
*actual* embedder (``dct_embedder.encode_jpeg``), not the older coefficient-slot
model:

    eligible_blocks(QF)                          # blocks with >= MIN_AC non-zero
                                                 # quantized AC coefficients
      x 1 bit each  - FRAMING_BITS (128)         # one parity bit per block
      -> channel-coded container bytes           # inverse of RS(255,223) expansion
      - container overhead (header + AES-GCM)    # framing [container.py]
      - container's own RS(255,223) expansion    # use_ecc=True
      = max TEXT_MESSAGE bytes                   # (compression optional/off)
      x preset.text_compression_factor           # TEXT_FILE only; 1.0 when
                                                 # NO_COMPRESSION (no DEFLATE)
"""
from __future__ import annotations

import math
from typing import Dict, List
import numpy as np

from ..container import (
    AES_GCM_OVERHEAD,
    FIXED_HEADER_SIZE,
    RS_K,
    RS_NSYM,
    CompressionPreset,
    container_overhead_bytes,
)
from ._dct import analyze_texture, rgb_to_luma
from .dct_embedder import FRAMING_BITS, MIN_AC
from .presets import (
    IMAGE_PRESETS,
    scaled_luma_table,
)

# Metadata byte budgets reserved when estimating capacity for payloads that
# carry a filename + mime type (TEXT_FILE). TEXT_MESSAGE carries neither.
_FILENAME_BUDGET = 64
_MIME_BUDGET = 32

#: Per-preset carrier reliability derate. The closed-loop embedder verifies
#: every bit against the re-quantized image, and low-QF covers leave some
#: carriers intrinsically unstable (clip-pinned blocks whose decoded feature
#: never settles). These factors are calibrated so the advertised capacity
#: embeds reliably in practice (worst observed across the validation matrix:
#: photo-like covers at the preset's target quality).
_PRESET_DERATE = {
    "light": 1.0,     # Q95: full reliability on real photos
    "standard": 0.6,  # Q85: ~60% of eligible carriers settle reliably
    "heavy": 0.4,     # Q75: ~40%
}


def _ecc_expand(n_bytes: int) -> int:
    """RS(255,223) coded size of ``n_bytes`` (matches ``reedsolo`` chunking)."""
    return n_bytes + ((n_bytes + RS_K - 1) // RS_K) * RS_NSYM


def _max_text_bytes(
    n_eligible: int,
    text_factor: float,
) -> Dict[str, int]:
    """Max payload bytes from the number of eligible carrier blocks.

    Each payload type is sized against its OWN container budget: TEXT_FILE
    pays the filename/mime metadata overhead (a few tens of bytes) but earns a
    compression multiplier; TEXT_MESSAGE pays none but is uncompressed. The
    metadata overhead means a TINY cover can carry more raw message bytes than
    raw file bytes; the compression multiplier overtakes once the cover is big
    enough.

    ``text_factor`` is the preset's ``text_compression_factor`` (1.0 for
    NO_COMPRESSION so TEXT_FILE is never inflated by a DEFLATE gain the channel
    does not apply).
    """
    if n_eligible <= FRAMING_BITS:
        return {"max_bytes_text_message": 0, "max_bytes_text_file": 0}
    coded_max = (n_eligible - FRAMING_BITS) // 8

    overhead_message = container_overhead_bytes(use_ecc=True, encrypted=True)
    overhead_file = container_overhead_bytes(
        original_filename="x" * _FILENAME_BUDGET,
        mime_type="x" * _MIME_BUDGET,
        use_ecc=True,
        encrypted=True,
    )

    def max_raw(fixed: int, ratio: float) -> int:
        # Largest original-payload size whose container (fixed overhead +
        # RS(255,223) expansion of the compressed bytes) fits in coded_max.
        # ``ratio`` is the preset's measured TEXT->DEFLATE factor (raw/deflated);
        # applied as a float so empirical sub-integer factors (e.g. 1.35) are
        # honoured instead of being truncated away by an integer division.
        lo, hi = 0, coded_max
        while lo < hi:
            mid = (lo + hi + 1) // 2
            coded = math.ceil(mid / ratio) if ratio > 1.0 else mid
            if fixed + _ecc_expand(coded) <= coded_max:
                lo = mid
            else:
                hi = mid - 1
        return lo

    return {
        "max_bytes_text_message": max_raw(overhead_message, 1.0),
        "max_bytes_text_file": max_raw(overhead_file, text_factor),
    }


def image_capacity(
    rgb: np.ndarray,
    compression_preset: CompressionPreset = CompressionPreset.NO_COMPRESSION,
) -> List[Dict]:
    """Capacity for every image preset under a channel compression preset.

    Args:
        rgb: HxWx3 (or HxW) uint8 cover image (RGB).
        compression_preset: channel-level preset governing the TEXT_FILE
            compression multiplier (default NO_COMPRESSION => factor 1.0).

    Returns:
        A list of per-preset dicts, each with ``max_bytes_text_message``,
        ``max_bytes_text_file``, ``expected_ber``, ``technique`` (+ diagnostics).
    """
    luma = rgb_to_luma(np.asarray(rgb))
    # NOTE (calibrated 2026-08-08): ``text_compression_factor`` is now the
    # empirically measured TEXT_FILE DEFLATE ratio (median 1.35 on the
    # deterministic synthetic corpus; see docs/COMPRESSION_PRESETS.md). Applied
    # as a float so sub-integer factors are honoured (measured median sits
    # below 2.0, which the legacy int() truncation would have discarded).
    text_factor = compression_preset.text_compression_factor

    results: List[Dict] = []
    for preset in IMAGE_PRESETS:
        quant = scaled_luma_table(preset.target_quality_factor)
        total_blocks, high_blocks, usable_slots = analyze_texture(luma, quant)

        # The engine's carrier eligibility: blocks with >= MIN_AC non-zero
        # quantized AC coefficients (one parity bit per block), derated for
        # the carriers that stay unstable at the preset's quality.
        n_eligible = int(_eligible_blocks(luma, quant) * _PRESET_DERATE.get(preset.id, 1.0))
        capacity = _max_text_bytes(n_eligible, text_factor)

        results.append({
            "id": preset.id,
            "name": preset.name,
            "description": preset.description,
            "technique": preset.technique,
            "target_quality_factor": preset.target_quality_factor,
            "expected_ber": preset.expected_ber,
            "survivability_description": preset.survivability_description,
            "compression_preset": compression_preset.value,
            "text_compression_factor": text_factor,
            **capacity,
            # diagnostics (handy for the evaluation harness / debugging)
            "total_blocks": total_blocks,
            "high_texture_blocks": high_blocks,
            "eligible_blocks": n_eligible,
            "usable_coeff_slots": usable_slots,
        })
    return results


def _eligible_blocks(luma: np.ndarray, quant_table: np.ndarray) -> int:
    """Blocks with at least ``MIN_AC`` non-zero quantized AC coefficients.

    Matches the embedder's carrier-eligibility rule (``dct_embedder``); the
    one-bit-per-block parity channel uses exactly these blocks in raster order.
    """
    h, w = luma.shape
    nby, nbx = h // 8, w // 8
    if nby == 0 or nbx == 0:
        return 0
    quant = np.round(_blockwise_dct2(luma) / quant_table[None, :, None, :])
    quant[:, 0, :, 0] = 0
    nz = np.count_nonzero(quant, axis=(1, 3))
    return int(np.count_nonzero(nz >= MIN_AC))


def _blockwise_dct2(luma: np.ndarray) -> np.ndarray:
    """Orthonormal 2-D DCT-II on every non-overlapping 8x8 block."""
    from scipy.fftpack import dct

    h, w = luma.shape
    nby, nbx = h // 8, w // 8
    blocks = (
        luma[: nby * 8, : nbx * 8].reshape(nby, 8, nbx, 8).astype(np.float64) - 128.0
    )
    return dct(dct(blocks, axis=1, norm="ortho"), axis=3, norm="ortho")
