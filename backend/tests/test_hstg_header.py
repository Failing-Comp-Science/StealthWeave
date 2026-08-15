"""Tests for sequential HSTG v1 header scan (short typed-message operating point)."""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from modules.container import CompressionPresetId, PayloadType, build_container
from modules.image_stego import LSBEmbedder
from modules.steganalysis.hstg_header import scan_sequential_hstg_header


def _textured(seed=1, size=256):
    rng = np.random.RandomState(seed)
    yy, xx = np.mgrid[0:size, 0:size]
    r = 120 + 40 * np.sin(xx / 9.0) + rng.randn(size, size) * 8
    g = 90 + 50 * np.cos(yy / 7.0) + rng.randn(size, size) * 8
    b = 140 + 35 * np.sin((xx + yy) / 11.0) + rng.randn(size, size) * 8
    return np.clip(np.stack([r, g, b], axis=-1), 0, 255).astype(np.uint8)


def test_hstg_header_clean_cover_not_found():
    img = _textured(seed=0)
    hit = scan_sequential_hstg_header(img)
    assert hit["found"] is False
    assert hit["detected"] is False
    assert hit["bits_per_channel"] is None


def test_hstg_header_finds_short_text_message():
    cover = _textured(seed=1)
    container = build_container(
        b"test hidden",
        PayloadType.TEXT_MESSAGE,
        compression_preset=CompressionPresetId.LIGHT,
        compress=True,
        use_ecc=True,
    )
    stego = LSBEmbedder(random_order=False, bits_per_channel=1).embed(
        cover, container, ""
    ).stego_media
    hit = scan_sequential_hstg_header(stego)
    assert hit["found"] is True
    assert hit["bits_per_channel"] == 1
    assert hit["payload_bytes"] >= 44
    assert hit["version"] == 1


def test_hstg_header_finds_text_file():
    cover = _textured(seed=2)
    container = build_container(
        b"notes\n" * 50,
        PayloadType.TEXT_FILE,
        compression_preset=CompressionPresetId.LIGHT,
        original_filename="notes.txt",
        mime_type="text/plain",
        compress=True,
        use_ecc=True,
    )
    stego = LSBEmbedder(random_order=False, bits_per_channel=1).embed(
        cover, container, ""
    ).stego_media
    hit = scan_sequential_hstg_header(stego)
    assert hit["found"] is True
    assert hit["bits_per_channel"] == 1


def test_hstg_header_misses_random_order():
    cover = _textured(seed=3)
    payload = os.urandom(400)
    stego = LSBEmbedder(random_order=True, bits_per_channel=1).embed(
        cover, payload, "key"
    ).stego_media
    hit = scan_sequential_hstg_header(stego)
    assert hit["found"] is False


def test_hstg_header_rejects_non_rgb():
    with pytest.raises(ValueError, match="RGB"):
        scan_sequential_hstg_header(np.zeros((8, 8), dtype=np.uint8))
