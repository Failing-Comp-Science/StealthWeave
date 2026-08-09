"""
Exact HSTG v2 channel accounting shared by the capacity calculators.

Maps the *usable carrier bits* a cover offers (eligible blocks for images,
mid-band DCT blocks summed over the I-frame grid for video) to the maximum
original payload bytes, mirroring the REAL embed chain instead of the
simplified two-term model the calculators previously used.

The embed chain for the DCT-QIM family (image + video) is:

    payload P
      -> [optional DEFLATE]                Q = deflate(P) iff it shrinks, else P
      -> container RS(255,223)             R = RS(Q)                  (use_ecc)
      -> v2 header + fname/mime + AES      C = header_bytes + R (+ GCM overhead)
      -> channel RS(255,223)               C' = RS(C)                 (outer layer)
      -> framing prefix                    FRAMING_BITS = 128
      -> embeddable bits = FRAMING_BITS + 8*len(C')

A cover with ``available_bits`` usable slots holds payload P iff

    FRAMING_BITS + 8 * rs_encoded_len(container_len(P)) <= available_bits

where
    container_len(P) = fixed_overhead + rs_encoded_len(compressed_len(P))
    compressed_len(P) = ceil(P / ratio)  when DEFLATE shrinks, else P

The image/video calculators previously constrained the *container* size
against ``(available_bits - FRAMING_BITS)//8``, omitting the OUTER channel
RS(255,223) expansion (~14.35%). That overstates capacity: a payload whose
container fits the raw budget is rejected by ``encode_jpeg`` / the video
engine, whose ``frame_bitstream`` re-encodes the whole container before
framing. This module restores that layer, so advertised capacity embeds
exactly at encode time.

The spatial (LSB) engine stores the container directly inside the v1 14-byte
``PayloadHeader`` (re-encrypted with AES-256-GCM, no channel RS / framing);
its budget is computed by :func:`spatial_container_budget` and fitted by
:func:`max_payload_from_container_bytes`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..base import PayloadHeader
from ..container import AES_GCM_OVERHEAD, rs_encoded_len
from ._channel import FRAMING_BITS


def compressed_len(payload_bytes: int, ratio: float) -> int:
    """Modeled DEFLATE output length: ceil(P/ratio) when ratio > 1.0 else P."""
    if ratio is None or ratio <= 1.0:
        return payload_bytes
    return math.ceil(payload_bytes / ratio)


def container_len(payload_bytes: int, fixed_overhead: int, ratio: float = 1.0) -> int:
    """Full HSTG v2 container length for a payload of ``payload_bytes``."""
    return fixed_overhead + rs_encoded_len(compressed_len(payload_bytes, ratio))


def channel_coded_len(container_length: int) -> int:
    """Length after the OUTER channel RS(255,223) layer."""
    return rs_encoded_len(container_length)


def required_bits(payload_bytes: int, fixed_overhead: int, ratio: float = 1.0) -> int:
    """Embeddable bits the real DCT pipeline needs for a payload."""
    return FRAMING_BITS + 8 * channel_coded_len(
        container_len(payload_bytes, fixed_overhead, ratio)
    )


def required_bits_for_container(container_length: int) -> int:
    """Embeddable bits the DCT-QIM pipeline needs for an ALREADY-serialized
    container of ``container_length`` bytes.

    Unlike :func:`required_bits` (which sizes from a raw payload + overhead +
    optional DEFLATE), this takes the exact serialized container the endpoint
    already built (``len(build_container(...))``) and returns the exact channel
    demand: the outer RS(255,223) expansion plus the 128-bit framing prefix.
    Used by the encode endpoints for the EXACT pre-embed fit check.
    """
    if container_length <= 0:
        return FRAMING_BITS
    return FRAMING_BITS + 8 * channel_coded_len(container_length)


def max_payload_channel_bits(
    available_bits: int, fixed_overhead: int = 0, ratio: float = 1.0
) -> int:
    """Largest payload that fits ``available_bits`` usable carrier slots.

    Mirrors the exact embed chain (container RS + channel RS + FRAMING_BITS):
    the payload embeds iff the pipeline needs no more slots than the cover
    offers. Returns 0 when the framing prefix alone does not fit.

    Args:
        available_bits: usable carrier slots the cover offers (>= 0).
        fixed_overhead: ``container_overhead_bytes(...)`` -- the payload-size
            independent header/filename/mime/AES-GCM cost.
        ratio: TEXT->DEFLATE factor (1.0 = no compression).
    """
    if available_bits <= FRAMING_BITS:
        return 0
    # Largest container length whose channel-coded form fits the budget.
    lo, hi = 0, available_bits  # upper bound: at least one payload byte
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if required_bits(mid, fixed_overhead, ratio) <= available_bits:
            lo = mid
        else:
            hi = mid - 1
    return lo


def spatial_container_budget(
    height: int, width: int, bits_per_channel: int = 1
) -> int:
    """Max HSTG container bytes the LSB engine can store in an HxW RGB cover.

    The LSB embedder (``modules.image_stego.lsb``) hides
    ``full_payload = v1_header(14) + AES_GCM(container)`` and requires
    ``len(full_payload) <= capacity(cover)`` where
    ``capacity = (H*W*3*bpc)//8 - 14`` (the header is subtracted again in the
    embedder's capacity). So the container itself may occupy at most

        (H*W*3*bpc)//8 - 14 (embedder capacity) - 14 (v1 header) - 44 (AES-GCM)

    Returns 0 for non-positive budgets.
    """
    budget_bytes = (height * width * 3 * bits_per_channel) // 8
    header = PayloadHeader.SIZE
    return max(0, budget_bytes - header - header - AES_GCM_OVERHEAD)


def max_payload_from_container_bytes(
    container_budget: int, fixed_overhead: int = 0, ratio: float = 1.0
) -> int:
    """Largest original payload whose container fits ``container_budget`` bytes.

    Used by the spatial (LSB) engine, which stores the container directly (v1
    header framing, no channel RS / FRAMING_BITS). ``fixed_overhead`` is the
    HSTG v2 ``container_overhead_bytes(...)`` for the target payload type.
    """
    if container_budget <= fixed_overhead:
        return 0
    lo, hi = 0, container_budget - fixed_overhead
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if container_len(mid, fixed_overhead, ratio) <= container_budget:
            lo = mid
        else:
            hi = mid - 1
    return lo


@dataclass(frozen=True)
class AccountingBreakdown:
    """Itemized overhead breakdown for a given payload size and channel preset.

    Used by the capacity and encode endpoints to expose exactly where every
    byte goes, so the UI can show an itemized accounting instead of a single
    opaque capacity number.
    """
    # Input parameters
    source_payload_bytes: int
    payload_compression_applied: bool
    compressed_payload_bytes: int
    compression_ratio: float

    # Container overhead
    container_header_bytes: int
    container_fname_mime_bytes: int
    container_aes_gcm_overhead: int
    container_rs_data_bytes: int
    container_rs_parity_bytes: int
    container_total_bytes: int

    # Channel framing + coding
    framing_bits: int
    channel_rs_data_bytes: int
    channel_rs_parity_bytes: int
    channel_total_bytes: int
    required_embedding_bits: int

    # Carrier capacity
    usable_carrier_capacity_bits: int
    usable_carrier_capacity_bytes: int

    # Margins
    capacity_margin_bytes: int
    capacity_margin_percent: float

    # Metadata
    capacity_model_version: str = "1.0"
    exact_or_estimated: str = "exact"  # "exact" for DCT-QIM path, "estimate" for preflight


def compute_accounting_breakdown(
    payload_bytes: int,
    fixed_overhead: int,
    ratio: float,
    available_bits: int,
    *,
    exact: bool = True,
) -> AccountingBreakdown:
    """Compute the full itemized accounting breakdown for a payload.

    Args:
        payload_bytes: Original payload size in bytes.
        fixed_overhead: Container overhead (header + fname/mime + AES-GCM).
        ratio: DEFLATE compression ratio (1.0 = no compression).
        available_bits: Usable carrier bits the cover offers.
        exact: If True, the breakdown reflects the exact embed chain.
               If False, it's a preflight estimate (ratio is a modeled median).

    Returns:
        An AccountingBreakdown with every overhead item separated.
    """
    payload_compression_applied = ratio > 1.0
    compressed = compressed_len(payload_bytes, ratio)

    # Container composition
    container_header = fixed_overhead  # includes header + fname/mime + AES-GCM
    container_rs_data = compressed
    container_rs_total = rs_encoded_len(compressed)
    container_rs_parity = container_rs_total - container_rs_data
    container_total = container_header + container_rs_total

    # Channel layer
    channel_rs_data = container_total
    channel_rs_total = rs_encoded_len(container_total)
    channel_rs_parity = channel_rs_total - channel_rs_data
    channel_total = channel_rs_total

    required = FRAMING_BITS + 8 * channel_total

    margin_bits = available_bits - required
    margin_bytes = max(0, margin_bits // 8)
    margin_percent = (margin_bytes / max(1, payload_bytes)) * 100.0 if payload_bytes > 0 else 0.0

    return AccountingBreakdown(
        source_payload_bytes=payload_bytes,
        payload_compression_applied=payload_compression_applied,
        compressed_payload_bytes=compressed,
        compression_ratio=ratio,
        container_header_bytes=container_header,
        container_fname_mime_bytes=0,  # folded into header; see note above
        container_aes_gcm_overhead=0,  # folded into header
        container_rs_data_bytes=container_rs_data,
        container_rs_parity_bytes=container_rs_parity,
        container_total_bytes=container_total,
        framing_bits=FRAMING_BITS,
        channel_rs_data_bytes=channel_rs_data,
        channel_rs_parity_bytes=channel_rs_parity,
        channel_total_bytes=channel_total,
        required_embedding_bits=required,
        usable_carrier_capacity_bits=available_bits,
        usable_carrier_capacity_bytes=available_bits // 8,
        capacity_margin_bytes=margin_bytes,
        capacity_margin_percent=round(margin_percent, 2),
        exact_or_estimated="exact" if exact else "estimate",
    )
