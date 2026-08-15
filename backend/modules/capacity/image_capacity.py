"""
Preset-aware image capacity calculator for the block-based DCT-QIM engine,
plus the lossless spatial (LSB) capacity model for PNG/BMP covers.

``image_capacity`` returns, for EVERY image preset in one call, the maximum
payload bytes a cover image can carry for each allowed payload type, computed
for a given channel-level :class:`CompressionPreset`. The model mirrors the
*actual* embedder (``dct_embedder.encode_jpeg``), not the older coefficient-slot
model:

    eligible_blocks(QF)                          # blocks with >= MIN_AC non-zero
                                                 # quantized AC coefficients
      x 1 bit each - FRAMING_BITS (128)          # one parity bit per block
      -> channel RS(255,223) + container RS      # exact chain [accounting.py]
      - container overhead (header + AES-GCM)    # framing [container.py]
      = max payload bytes                        # (compression optional/off)

``spatial_capacity`` covers PNG/BMP, which the API routes to the lossless LSB
engine: the full HxWx3 LSB budget is available (minus the v1 framing header
and the AES-256-GCM wrapper), so a 512x512 PNG holds tens of kilobytes rather
than the ~hundreds of bytes the JPEG model would claim. The two models share
their exact fit math via ``modules.capacity.accounting``.
"""
from __future__ import annotations

from typing import Dict, List
import numpy as np

from ..container import (
    CompressionPreset,
    container_overhead_bytes,
)
from ._dct import (
    _blockwise_dct2,
    analyze_texture_from_coeffs,
    rgb_to_luma,
)
from .accounting import (
    max_payload_channel_bits,
    max_payload_from_container_bytes,
    spatial_container_budget,
)
from .dct_embedder import MIN_AC
from .presets import (
    IMAGE_COMPRESSION_RATIO,
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

#: The LOSSLESS carrier preset used for PNG/BMP covers (spatial LSB engine).
LOSSLESS_PRESET_ID = "lossless_high_capacity"


def _overheads() -> tuple[int, int]:
    """(TEXT_MESSAGE, TEXT_FILE) container overheads in bytes."""
    return (
        container_overhead_bytes(use_ecc=True, encrypted=True),
        container_overhead_bytes(
            original_filename="x" * _FILENAME_BUDGET,
            mime_type="x" * _MIME_BUDGET,
            use_ecc=True,
            encrypted=True,
        ),
    )


def _max_text_bytes(
    n_eligible: int,
    text_factor: float,
) -> Dict[str, int]:
    """Max payload bytes from the number of eligible carrier blocks.

    Uses the exact channel accounting (``accounting.max_payload_channel_bits``):
    the number of usable slots is converted to payload bytes by inverting the
    full embed chain (container RS + channel RS + FRAMING_BITS), so a payload
    of the advertised size embeds exactly at encode time.

    Each payload type is sized against its OWN container budget: TEXT_FILE
    pays the filename/mime metadata overhead (a few tens of bytes) but earns a
    compression multiplier; TEXT_MESSAGE pays none but is uncompressed.

    ``text_factor`` is the preset's ``text_compression_factor`` (1.0 for
    NO_COMPRESSION so TEXT_FILE is never inflated by a DEFLATE gain the channel
    does not apply).
    """
    overhead_message, overhead_file = _overheads()
    return {
        "max_bytes_text_message": max_payload_channel_bits(
            n_eligible, overhead_message, ratio=1.0
        ),
        "max_bytes_text_file": max_payload_channel_bits(
            n_eligible, overhead_file, ratio=text_factor
        ),
        # IMAGE pays the same filename/mime overhead as TEXT_FILE; LOSSLESS
        # (and JPEG DCT) model it at IMAGE_COMPRESSION_RATIO (1.0).
        "max_bytes_image": max_payload_channel_bits(
            n_eligible, overhead_file, ratio=IMAGE_COMPRESSION_RATIO
        ),
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
    # deterministic synthetic corpus; see COMPRESSION_PRESETS.md). Applied
    # as a float so sub-integer factors are honoured (measured median sits
    # below 2.0, which the legacy int() truncation would have discarded).
    text_factor = compression_preset.text_compression_factor

    # Phase 1.6: compute the block-DCT ONCE and re-quantize per preset (the old
    # loop ran the full DCT twice per preset — ~6x on 4K).
    coeffs = _blockwise_dct2(luma)

    results: List[Dict] = []
    for preset in IMAGE_PRESETS:
        quant = scaled_luma_table(preset.target_quality_factor)
        total_blocks, high_blocks, usable_slots = analyze_texture_from_coeffs(coeffs, quant)

        # The engine's carrier eligibility: blocks with >= MIN_AC non-zero
        # quantized AC coefficients (one parity bit per block), derated for
        # the carriers that stay unstable at the preset's quality.
        n_eligible = int(_eligible_from_coeffs(coeffs, quant) * _PRESET_DERATE.get(preset.id, 1.0))
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
    return _eligible_from_coeffs(_blockwise_dct2(luma), quant_table)


def _eligible_from_coeffs(coeffs: np.ndarray, quant_table: np.ndarray) -> int:
    """Like :func:`_eligible_blocks`, but from PRE-COMPUTED block DCT coeffs
    so a multi-preset capacity call only runs the DCT once (Phase 1.6)."""
    nby, _, nbx, _ = coeffs.shape
    if nby == 0 or nbx == 0:
        return 0
    quant = np.round(coeffs / quant_table[None, :, None, :])
    quant[:, 0, :, 0] = 0
    nz = np.count_nonzero(quant, axis=(1, 3))
    return int(np.count_nonzero(nz >= MIN_AC))


def _derate_for_qf(quality_factor: int) -> float:
    """Reliability derate for a JPEG quality factor (matches ``_PRESET_DERATE``).

    Keyed by the same QF thresholds the encode endpoint uses to pick the
    carrier preset (light>=90, standard>=80, else heavy), so the capacity model
    and the encode-time exact fit check agree on how many carriers are usable.
    """
    if quality_factor >= 90:
        return _PRESET_DERATE["light"]
    if quality_factor >= 80:
        return _PRESET_DERATE["standard"]
    return _PRESET_DERATE["heavy"]


def dct_eligible_bits(rgb: np.ndarray, quality_factor: int) -> int:
    """Usable DCT-QIM carrier bits (1 bit/block) for a cover at ``quality_factor``.

    Returns the SAME derated eligible-block count the capacity model advertises
    (``_eligible_blocks`` x reliability derate), so the encode endpoint's exact
    pre-embed fit check rejects a container exactly when ``image_capacity`` says
    it does not fit and accepts it exactly when the model says it does.
    """
    luma = rgb_to_luma(np.asarray(rgb))
    quant = scaled_luma_table(int(quality_factor))
    return int(_eligible_blocks(luma, quant) * _derate_for_qf(int(quality_factor)))


def spatial_capacity(
    rgb: np.ndarray,
    compression_preset: CompressionPreset = CompressionPreset.NO_COMPRESSION,
    bits_per_channel: int = 1,
) -> List[Dict]:
    """Capacity for a PNG/BMP cover under the lossless spatial (LSB) engine.

    PNG/BMP covers never reach the JPEG DCT engine (``_detect_image_engine``
    routes them to ``LSBEmbedder``), so their capacity is the LSB channel's:
    the full HxWx3 bit budget minus the v1 framing header and the AES-256-GCM
    wrapper the embedder adds, fitted with the exact container accounting.
    The LSB channel stores the container directly (no channel RS / framing),
    so the whole budget converts to payload bytes.

    Returns a single-element list describing the LOSSLESS carrier preset, so
    the API can expose it alongside (or instead of) the JPEG presets.

    Args:
        rgb: HxWx3 uint8 cover image (RGB).
        compression_preset: channel-level preset governing the TEXT_FILE
            compression multiplier (default NO_COMPRESSION => factor 1.0).
        bits_per_channel: LSB planes used (the engine's default is 1; the
            embedder auto-raises it if a payload needs more room).
    """
    rgb = np.asarray(rgb)
    h, w = rgb.shape[:2]
    container_budget = spatial_container_budget(h, w, bits_per_channel)
    overhead_message, overhead_file = _overheads()
    text_factor = compression_preset.text_compression_factor
    return [
        {
            "id": LOSSLESS_PRESET_ID,
            "name": "Lossless (PNG/BMP)",
            "description": (
                "Lossless LSB embedding: full spatial capacity, byte-exact "
                "recovery, no re-compression loss."
            ),
            "technique": "Spatial LSB over all RGB channels (bit-plane 1)",
            "target_quality_factor": 100,
            "expected_ber": 0.0,
            "survivability_description": "Survives: lossless round-trips (PNG/BMP re-save), no lossy re-encode",
            "compression_preset": compression_preset.value,
            "text_compression_factor": text_factor,
            "max_bytes_text_message": max_payload_from_container_bytes(
                container_budget, overhead_message, ratio=1.0
            ),
            "max_bytes_text_file": max_payload_from_container_bytes(
                container_budget, overhead_file, ratio=text_factor
            ),
            "max_bytes_image": max_payload_from_container_bytes(
                container_budget, overhead_file, ratio=IMAGE_COMPRESSION_RATIO
            ),
            "total_blocks": (h // 8) * (w // 8),
            "high_texture_blocks": None,
            "eligible_blocks": h * w * 3,
            "usable_coeff_slots": h * w * 3,
        }
    ]


def _blockwise_dct2(luma: np.ndarray) -> np.ndarray:
    """Orthonormal 2-D DCT-II on every non-overlapping 8x8 block."""
    from scipy.fftpack import dct

    h, w = luma.shape
    nby, nbx = h // 8, w // 8
    blocks = (
        luma[: nby * 8, : nbx * 8].reshape(nby, 8, nbx, 8).astype(np.float64) - 128.0
    )
    return dct(dct(blocks, axis=1, norm="ortho"), axis=3, norm="ortho")
