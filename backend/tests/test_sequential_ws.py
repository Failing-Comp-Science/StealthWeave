"""
Tests for sequential Weighted Stego (Ker) + prefix-scan helpers.

Validates raster flattening, predictor borders, BH correction, clean-cover
behavior, known sequential LSB, negative controls, and runtime.
"""
from __future__ import annotations

import io
import os
import sys
import time

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from modules.image_stego import LSBEmbedder
from modules.steganalysis.prefix_scan import (
    benjamini_hochberg,
    flatten_channel,
    logarithmic_grid,
    prefix_mask_bool,
    prefix_masks,
    window_candidates,
)
from modules.steganalysis.sequential_ws import (
    IMPLEMENTATION_VERSION,
    SequentialWS,
    predict_cover,
    ws_pixel_terms,
)


def _natural_image(seed=0, size=256):
    rng = np.random.RandomState(seed)
    x = np.linspace(0, 1, size)
    y = np.linspace(0, 1, size)
    xx, yy = np.meshgrid(x, y)
    base = 128 + 60 * np.sin(3 * xx) * np.cos(2 * yy)
    img = np.stack([base, base * 0.9, base * 1.1], axis=-1)
    img += rng.randn(size, size, 3) * 3
    return np.clip(img, 0, 255).astype(np.uint8)


def _textured_image(seed=1, size=256):
    rng = np.random.RandomState(seed)
    yy, xx = np.mgrid[0:size, 0:size]
    r = 120 + 40 * np.sin(xx / 9.0) + rng.randn(size, size) * 8
    g = 90 + 50 * np.cos(yy / 7.0) + rng.randn(size, size) * 8
    b = 140 + 35 * np.sin((xx + yy) / 11.0) + rng.randn(size, size) * 8
    return np.clip(np.stack([r, g, b], axis=-1), 0, 255).astype(np.uint8)


def _lsb_matching_prefix(cover: np.ndarray, n_bits: int, seed: int = 0) -> np.ndarray:
    """Evaluation-only ±1 embedding on a raster prefix. Not a product embedder."""
    rng = np.random.RandomState(seed)
    stego = cover.copy()
    flat = stego.reshape(-1).astype(np.int16)
    n_bits = min(int(n_bits), flat.size)
    bits = rng.randint(0, 2, size=n_bits)
    idx = np.arange(n_bits)
    cur_lsb = flat[idx] & 1
    flip = bits != cur_lsb
    direction = rng.choice(np.array([-1, 1], dtype=np.int16), size=n_bits)
    direction = np.where(flat[idx] == 0, 1, direction)
    direction = np.where(flat[idx] == 255, -1, direction)
    flat[idx] = np.where(flip, flat[idx] + direction, flat[idx])
    return np.clip(flat, 0, 255).astype(np.uint8).reshape(cover.shape)


# ---------------------------------------------------------------------------
# Raster flatten / masks / BH
# ---------------------------------------------------------------------------

def test_flatten_channel_raster_order():
    img = np.arange(2 * 3 * 3, dtype=np.uint8).reshape(2, 3, 3)
    red = flatten_channel(img, 0)
    assert red.tolist() == [int(img[y, x, 0]) for y in range(2) for x in range(3)]
    assert flatten_channel(img, 1)[0] == int(img[0, 0, 1])
    assert flatten_channel(img, 2)[-1] == int(img[1, 2, 2])


def test_logarithmic_grid_includes_min_and_full():
    grid = logarithmic_grid(10000, candidate_min=256)
    assert grid[0] == 256
    assert grid[-1] == 10000
    assert grid == sorted(grid)
    assert len(grid) >= 2


def test_logarithmic_grid_n_candidates():
    grid = logarithmic_grid(4096, candidate_min=256, n_candidates=5)
    assert grid[0] == 256
    assert grid[-1] == 4096
    assert len(grid) <= 6  # requested 5 plus possible min/max inserts


def test_prefix_mask_generation():
    w = prefix_mask_bool(10, 4)
    assert w.dtype == np.bool_
    assert w.tolist() == [True, True, True, True, False, False, False, False, False, False]
    assert prefix_masks(10, [3, 3, 12]) == [(0, 3), (0, 10)]


def test_window_candidates_are_contiguous():
    wins = window_candidates(1024, [256, 512], max_windows=16)
    assert wins
    for start, end in wins:
        assert 0 <= start < end <= 1024
        assert (end - start) in (256, 512)
    assert (0, 256) in wins


def test_benjamini_hochberg_known_vector():
    raw = [0.001, 0.01, 0.04, 0.2, 0.8]
    adj = benjamini_hochberg(raw)
    assert adj.shape == (5,)
    assert np.all(adj >= np.asarray(raw) - 1e-15)
    assert np.all(adj <= 1.0)
    # Monotone in the sorted order: BH never increases rank-order violations
    # of the raw list after sorting.
    order = np.argsort(raw)
    assert np.all(np.diff(adj[order]) >= -1e-12)
    empty = benjamini_hochberg([])
    assert empty.size == 0


# ---------------------------------------------------------------------------
# Predictor
# ---------------------------------------------------------------------------

def test_predictor_uint8_to_float64_and_no_wraparound():
    ch = np.zeros((3, 3), dtype=np.uint8)
    ch[0, 0] = 10
    ch[0, 2] = 200
    ch[2, 0] = 30
    ch[2, 2] = 250
    hat = predict_cover(ch, "four_neighbor_raw")
    assert hat.dtype == np.float64
    # Corner (0,0) uses right=0 and down=0 only — not the opposite edge 200/30.
    assert hat[0, 0] == pytest.approx(0.0)
    # Center uses all four neighbors (all zero except we didn't set them).
    assert hat[1, 1] == pytest.approx(0.0)


def test_predictor_excludes_pixel_itself():
    ch = np.full((3, 3), 10, dtype=np.uint8)
    ch[1, 1] = 200
    hat = predict_cover(ch, "four_neighbor_raw")
    assert hat[1, 1] == pytest.approx(10.0)


def test_predictor_msb_uses_pov_midpoints():
    ch = np.array([[10, 11], [12, 13]], dtype=np.uint8)
    hat = predict_cover(ch, "four_neighbor_msb")
    # Neighbors of (0,0): right 11 → PoV midpoint 10.5; down 12 → 12.5.
    assert hat[0, 0] == pytest.approx((10.5 + 12.5) / 2.0)
    assert np.issubdtype(hat.dtype, np.floating)


def test_predictor_unknown_raises():
    with pytest.raises(ValueError, match="Unknown predictor"):
        predict_cover(np.zeros((4, 4), dtype=np.uint8), "not_a_predictor")  # type: ignore[arg-type]


def test_ws_terms_length_matches_raster():
    ch = np.arange(12, dtype=np.uint8).reshape(3, 4)
    hat = predict_cover(ch, "four_neighbor_msb")
    terms = ws_pixel_terms(ch, hat)
    assert terms.shape == (12,)
    assert terms.dtype == np.float64


def test_ws_terms_drop_saturated_samples():
    ch = np.array([[0, 10], [20, 255]], dtype=np.uint8)
    hat = predict_cover(ch, "four_neighbor_raw")
    terms = ws_pixel_terms(ch, hat)
    assert np.isnan(terms[0])  # 0
    assert np.isnan(terms[3])  # 255
    assert np.isfinite(terms[1])
    assert np.isfinite(terms[2])


# ---------------------------------------------------------------------------
# Detector behavior
# ---------------------------------------------------------------------------

def test_sequential_ws_clean_cover_not_suspicious():
    img = _natural_image(seed=0)
    result = SequentialWS.detect(img)
    assert result.decision in ("clean", "inconclusive")
    assert result.detected is False
    assert result.implementation_version == IMPLEMENTATION_VERSION
    assert result.detector == "sequential_ws"
    assert result.candidate_curve


def test_sequential_ws_detects_sequential_lsb_and_estimates_prefix():
    img = _natural_image(seed=3)
    payload = os.urandom(2500)
    stego = LSBEmbedder(random_order=False, bits_per_channel=1).embed(
        img, payload, "key"
    ).stego_media
    result = SequentialWS.detect(stego)
    assert result.decision == "suspicious"
    assert result.detected is True
    assert result.estimated_prefix_samples is not None
    assert result.estimated_payload_bits is not None
    # Interleaved 2500 bytes → 20000 bits → ~6667 samples/channel.
    expected = (len(payload) * 8) // 3
    assert abs(result.estimated_prefix_samples - expected) < expected * 0.75
    assert result.estimated_payload_bits > 0


def test_sequential_ws_schema_and_channel_scores():
    img = _natural_image(seed=2)
    result = SequentialWS.detect(img)
    assert set(result.channel_scores) == {"red", "green", "blue"}
    assert result.runtime_ms >= 0.0
    assert result.limitations
    for pt in result.candidate_curve:
        assert pt.end > 0
        assert np.isfinite(pt.raw_score)


def test_sequential_ws_flat_cover_not_suspicious():
    img = np.full((256, 256, 3), 128, dtype=np.uint8)
    result = SequentialWS.detect(img)
    assert result.decision != "suspicious"
    assert result.detected is False


def test_sequential_ws_saturated_cover_not_auto_flagged():
    img = np.zeros((256, 256, 3), dtype=np.uint8)
    img[:80] = 252
    img[80:176] = 90
    img[176:] = 4
    rng = np.random.RandomState(11)
    img = np.clip(img.astype(np.int16) + rng.randint(-2, 3, img.shape), 0, 255).astype(np.uint8)
    result = SequentialWS.detect(img)
    assert result.decision != "suspicious"


def test_sequential_ws_png_roundtrip_stays_clean():
    img = _natural_image(seed=4)
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="PNG")
    buf.seek(0)
    with Image.open(buf) as im:
        roundtrip = np.asarray(im.convert("RGB"))
    result = SequentialWS.detect(roundtrip)
    assert result.decision != "suspicious"


def test_sequential_ws_random_order_not_tight_prefix():
    rng = np.random.RandomState(5)
    img = _natural_image(seed=5)
    payload = rng.randint(0, 256, 2500).astype(np.uint8).tobytes()
    seq = LSBEmbedder(random_order=False, bits_per_channel=1).embed(
        img, payload, "key"
    ).stego_media
    rnd = LSBEmbedder(random_order=True, bits_per_channel=1).embed(
        img, payload, "key"
    ).stego_media
    ws_seq = SequentialWS.detect(seq)
    ws_rnd = SequentialWS.detect(rnd)
    assert ws_seq.decision == "suspicious"
    true_len = (len(payload) * 8) // 3
    seq_err = abs((ws_seq.estimated_prefix_samples or 0) - true_len)
    if ws_rnd.decision == "suspicious":
        rnd_err = abs((ws_rnd.estimated_prefix_samples or 0) - true_len)
        assert rnd_err >= seq_err
        assert ws_seq.score >= ws_rnd.score - 1e-6
    else:
        assert ws_rnd.detected is False


def test_sequential_ws_lsb_matching_not_claimed_as_replacement():
    img = _textured_image(seed=6)
    n_bits = 2500 * 8
    stego = _lsb_matching_prefix(img, n_bits, seed=6)
    seq = LSBEmbedder(random_order=False, bits_per_channel=1).embed(
        img, os.urandom(2500), "key"
    ).stego_media
    ws_match = SequentialWS.detect(stego)
    ws_repl = SequentialWS.detect(seq)
    # Replacement is the threat model; matching must not look stronger.
    assert ws_repl.score >= ws_match.score - 1e-6
    if ws_match.decision == "suspicious":
        assert ws_repl.decision == "suspicious"


def test_sequential_ws_rejects_non_rgb():
    with pytest.raises(ValueError, match="RGB"):
        SequentialWS.detect(np.zeros((8, 8), dtype=np.uint8))


def test_sequential_ws_tiny_image_inconclusive():
    tiny = np.zeros((8, 8, 3), dtype=np.uint8)
    result = SequentialWS.detect(tiny)
    assert result.decision == "inconclusive"
    assert result.detected is False


def test_sequential_ws_window_mode_runs():
    img = _natural_image(seed=8)
    result = SequentialWS.detect(img, mode="window")
    assert result.mode == "window"
    assert result.decision in ("clean", "suspicious", "inconclusive")
    assert result.candidate_curve


def test_sequential_ws_runtime_512_under_two_seconds():
    img = _natural_image(seed=9, size=512)
    t0 = time.perf_counter()
    result = SequentialWS.detect(img)
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0
    assert result.runtime_ms < 2000.0
