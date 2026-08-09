"""
Tests for the exact channel accounting module (modules/capacity/accounting.py)
and the spatial (LSB) image capacity model (modules/capacity/image_capacity.py).

These lock in the Phase-1 refactor: advertised capacity is now computed by
inverting the REAL embed chain (container RS + channel RS + FRAMING_BITS), so a
payload of the advertised size embeds exactly at encode time, and PNG/BMP
covers report their true spatial capacity instead of the JPEG DCT model's
~hundreds-of-bytes estimate.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

from modules.base import PayloadHeader
from modules.capacity.accounting import (
    container_len,
    max_payload_channel_bits,
    max_payload_from_container_bytes,
    required_bits,
    spatial_container_budget,
)
from modules.capacity.image_capacity import (
    LOSSLESS_PRESET_ID,
    image_capacity,
    spatial_capacity,
)
from modules.container import (
    AES_GCM_OVERHEAD,
    CompressionPreset,
    container_overhead_bytes,
    rs_encoded_len,
)


def _textured_image(h=512, w=512, seed=0):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, (h, w, 3), dtype=np.uint8)


# --------------------------------------------------------------------------
# Exact accounting invariants
# --------------------------------------------------------------------------

def test_required_bits_is_exact_pipeline_length():
    # The embed chain for a payload is FRAMING_BITS + 8 * channel_rs(container),
    # where the container itself carries an inner RS expansion.
    payload, overhead = 5000, 94
    bits = required_bits(payload, overhead, ratio=1.0)
    container = container_len(payload, overhead, ratio=1.0)
    assert container == overhead + rs_encoded_len(payload)
    assert bits == 128 + 8 * rs_encoded_len(container)


def test_max_payload_channel_bits_roundtrips_required_bits():
    # A payload of the advertised size must embed exactly within the slot budget
    # (when any payload fits at all; tiny budgets may hold only the framing +
    # overhead, in which case the model honestly returns 0).
    for slots in (1000, 5000, 20000, 100000):
        p = max_payload_channel_bits(slots, 94, ratio=1.0)
        if p > 0:
            assert required_bits(p, 94, ratio=1.0) <= slots
            # And one byte more no longer fits (binary search is exact).
            assert required_bits(p + 1, 94, ratio=1.0) > slots
        else:
            assert required_bits(1, 94, ratio=1.0) > slots


def test_max_payload_channel_bits_monotonic():
    assert (
        max_payload_channel_bits(20000, 94)
        >= max_payload_channel_bits(10000, 94)
    )


def test_max_payload_channel_bits_zero_when_framing_exceeds():
    assert max_payload_channel_bits(128, 94) == 0
    assert max_payload_channel_bits(10, 94) == 0


def test_compression_ratio_increases_file_capacity():
    slots = 50000
    plain = max_payload_channel_bits(slots, 190, ratio=1.0)
    chat = max_payload_channel_bits(slots, 190, ratio=1.35)
    assert chat >= plain


# --------------------------------------------------------------------------
# Spatial (LSB) budget
# --------------------------------------------------------------------------

def test_spatial_container_budget_matches_lsb_embedder_constraint():
    # LSB embedder: full_payload = v1_header(14) + AES_GCM(container); requires
    # len(full_payload) <= (H*W*3*bpc)//8 - 14 (its own capacity()).
    h, w, bpc = 96, 96, 1
    budget = spatial_container_budget(h, w, bpc)
    total_bytes = (h * w * 3 * bpc) // 8
    assert budget == total_bytes - PayloadHeader.SIZE - PayloadHeader.SIZE - AES_GCM_OVERHEAD
    # The biggest container that fits really fits the embedder check:
    assert 14 + AES_GCM_OVERHEAD + budget <= total_bytes - PayloadHeader.SIZE
    assert 14 + AES_GCM_OVERHEAD + budget + 1 > total_bytes - PayloadHeader.SIZE


def test_spatial_budget_scales_with_bits_per_channel():
    base = spatial_container_budget(256, 256, 1)
    tripled = spatial_container_budget(256, 256, 3)
    # The fixed header/GCM deduction is applied once per bpc level, so tripling
    # bpc triples the raw budget but subtracts the same (now amortized) overhead.
    assert tripled == 3 * base + 2 * (2 * PayloadHeader.SIZE + AES_GCM_OVERHEAD)


# --------------------------------------------------------------------------
# Spatial image capacity model
# --------------------------------------------------------------------------

def test_spatial_capacity_reports_lossless_preset():
    caps = spatial_capacity(_textured_image())
    assert len(caps) == 1
    c = caps[0]
    assert c["id"] == LOSSLESS_PRESET_ID
    assert c["expected_ber"] == 0.0
    assert c["target_quality_factor"] == 100
    assert c["max_bytes_text_message"] >= 0
    assert c["max_bytes_text_file"] >= 0


def test_spatial_capacity_is_much_larger_than_jpeg_model():
    rgb = _textured_image(512, 512)
    spatial = spatial_capacity(rgb)[0]
    jpeg = {c["id"]: c for c in image_capacity(rgb)}
    # The LSB channel offers the full HxWx3 budget; the JPEG model reports only
    # high-texture DCT blocks. A 512x512 PNG must hold tens of kilobytes, not
    # the ~hundreds of bytes the JPEG model claimed.
    assert spatial["max_bytes_text_message"] > 50_000
    assert spatial["max_bytes_text_message"] > 50 * jpeg["light"]["max_bytes_text_message"]


def test_spatial_capacity_text_file_bounds():
    caps = spatial_capacity(_textured_image(256, 256))
    c = caps[0]
    # TEXT_FILE pays metadata overhead but NO_COMPRESSION gives no DEFLATE gain.
    assert c["max_bytes_text_file"] <= c["max_bytes_text_message"]


def test_spatial_capacity_scales_with_resolution():
    small = spatial_capacity(_textured_image(128, 128))[0]
    large = spatial_capacity(_textured_image(512, 512))[0]
    assert large["max_bytes_text_message"] > small["max_bytes_text_message"]


def test_spatial_capacity_default_is_no_compression():
    c = spatial_capacity(_textured_image(128, 128))[0]
    assert c["compression_preset"] == CompressionPreset.NO_COMPRESSION.value
    assert c["text_compression_factor"] == 1.0


def test_spatial_container_fit_matches_model():
    # Sanity: the max TEXT_MESSAGE payload's container fits the LSB budget.
    rgb = _textured_image(96, 96)
    c = spatial_capacity(rgb)[0]
    budget = spatial_container_budget(96, 96, 1)
    overhead = container_overhead_bytes(use_ecc=True, encrypted=True)
    assert container_len(c["max_bytes_text_message"], overhead, ratio=1.0) <= budget
