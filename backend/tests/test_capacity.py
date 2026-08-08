"""
Tests for the preset-aware capacity calculator (modules/capacity/).

Covers image + video capacity across ALL presets (>=3 each), the JPEG
quality-factor scaling from libjpeg, monotonic capacity ordering, and the
expected result-shape the API/UI depend on.
"""
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

from modules.capacity import (
    image_capacity,
    video_capacity,
    IMAGE_PRESETS,
    VIDEO_PRESETS,
    CompressionPreset,
)
from modules.capacity.presets import scaled_luma_table, JPEG_LUMA_Q, TEXT_COMPRESSION_RATIO
from modules.capacity._dct import analyze_texture, rgb_to_luma
from modules.container import TEXT_COMPRESSION_FACTOR_CHAT


# --------------------------------------------------------------------------
# JPEG quantization scaling [IJG jpeg_quality_scaling]
# --------------------------------------------------------------------------

def test_quality_scaling_q50_is_base_table():
    # At QF=50 scale == 100, so table == base Annex-K table.
    t50 = scaled_luma_table(50)
    assert np.array_equal(t50, JPEG_LUMA_Q)


def test_quality_scaling_monotonic():
    # Higher QF -> finer (smaller) quantizer entries -> more coeffs survive.
    t95 = scaled_luma_table(95)
    t75 = scaled_luma_table(75)
    assert t95.mean() < t75.mean()
    # All entries clamped to [1, 255].
    assert t95.min() >= 1 and t95.max() <= 255


def test_texture_analysis_flat_vs_noisy():
    flat = np.full((64, 64), 128.0)
    quant = scaled_luma_table(85)
    total, high, slots = analyze_texture(flat, quant)
    assert total == 64  # 8x8 grid of 8x8 blocks
    assert high == 0 and slots == 0  # flat image has no AC energy

    rng = np.random.default_rng(0)
    noisy = rng.integers(0, 256, (64, 64)).astype(float)
    total_n, high_n, slots_n = analyze_texture(noisy, quant)
    assert high_n > 0 and slots_n > 0


# --------------------------------------------------------------------------
# Image capacity
# --------------------------------------------------------------------------

def _textured_image(h=512, w=512, seed=0):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, (h, w, 3), dtype=np.uint8)


def test_image_capacity_returns_all_presets():
    caps = image_capacity(_textured_image())
    assert len(caps) == len(IMAGE_PRESETS) >= 3
    ids = {c["id"] for c in caps}
    assert ids == {"light", "standard", "heavy"}


def test_image_capacity_result_shape():
    caps = image_capacity(_textured_image())
    for c in caps:
        for key in (
            "id", "name", "description", "technique", "expected_ber",
            "survivability_description", "target_quality_factor",
            "max_bytes_text_message", "max_bytes_text_file",
            "compression_preset", "text_compression_factor",
        ):
            assert key in c, f"missing {key}"
        assert c["max_bytes_text_message"] >= 0
        assert c["max_bytes_text_file"] >= 0
        # Compression can at most multiply the message capacity by the ratio;
        # the TEXT_FILE metadata overhead makes tiny covers message-heavy.
        assert (
            c["max_bytes_text_file"]
            <= math.ceil(c["max_bytes_text_message"] * TEXT_COMPRESSION_RATIO)
        )


def test_image_capacity_no_compression_does_not_apply_2_5x():
    # NO_COMPRESSION must NOT multiply TEXT_FILE capacity by the legacy 2.5x.
    # The uncompressed channel carries the raw payload bytes, so TEXT_FILE
    # capacity can never exceed TEXT_MESSAGE capacity (it only pays extra
    # filename/mime metadata overhead).
    rgb = _textured_image()
    caps = {c["id"]: c for c in image_capacity(rgb, compression_preset=CompressionPreset.NO_COMPRESSION)}
    for c in caps.values():
        assert c["compression_preset"] == "no_compression"
        assert c["text_compression_factor"] == 1.0
        assert c["max_bytes_text_file"] <= c["max_bytes_text_message"]


def test_image_capacity_chat_preset_capacity_gte_no_compression():
    # For the same synthetic cover + TEXT_FILE, a compressed channel preset
    # (which may DEFLATE) must offer >= capacity than NO_COMPRESSION (which
    # never does), for every carrier preset.
    rgb = _textured_image()
    none = {c["id"]: c for c in image_capacity(rgb, compression_preset=CompressionPreset.NO_COMPRESSION)}
    for chat in (CompressionPreset.CHAT_STANDARD, CompressionPreset.CHAT_HD):
        chat_caps = {c["id"]: c for c in image_capacity(rgb, compression_preset=chat)}
        for cid, c in chat_caps.items():
            assert c["compression_preset"] == chat.value
            assert c["text_compression_factor"] == TEXT_COMPRESSION_FACTOR_CHAT
            assert (
                c["max_bytes_text_file"]
                >= none[cid]["max_bytes_text_file"]
            ), f"{chat} {cid} under-caps NO_COMPRESSION"


def test_image_capacity_default_is_no_compression():
    # Backward-compat default: a bare call keeps the honest 1.0x assumption
    # rather than silently applying the global 2.5x.
    caps = image_capacity(_textured_image())
    assert all(c["compression_preset"] == "no_compression" for c in caps)
    assert all(c["text_compression_factor"] == 1.0 for c in caps)


def test_image_capacity_monotonic_by_preset():
    caps = {c["id"]: c for c in image_capacity(_textured_image())}
    # Higher QF preset (light=Q95) carries more than robust (heavy=Q75).
    assert (
        caps["light"]["max_bytes_text_message"]
        >= caps["standard"]["max_bytes_text_message"]
        >= caps["heavy"]["max_bytes_text_message"]
    )
    # Expected BER increases as robustness target loosens.
    assert (
        caps["light"]["expected_ber"]
        <= caps["standard"]["expected_ber"]
        <= caps["heavy"]["expected_ber"]
    )


def test_image_capacity_scales_with_resolution():
    small = image_capacity(_textured_image(256, 256))
    large = image_capacity(_textured_image(512, 512))
    small_by_id = {c["id"]: c for c in small}
    large_by_id = {c["id"]: c for c in large}
    for pid in ("light", "standard", "heavy"):
        assert (
            large_by_id[pid]["max_bytes_text_message"]
            > small_by_id[pid]["max_bytes_text_message"]
        )


def test_image_capacity_grayscale_input():
    rng = np.random.default_rng(2)
    gray = rng.integers(0, 256, (128, 128), dtype=np.uint8)
    caps = image_capacity(gray)
    assert len(caps) == 3


# --------------------------------------------------------------------------
# Video capacity
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sample_video():
    cv2 = pytest.importorskip("cv2")
    rng = np.random.default_rng(1)
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.close()
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    vw = cv2.VideoWriter(tmp.name, fourcc, 25.0, (320, 240))
    assert vw.isOpened(), "OpenCV could not open an mp4 writer"
    for _ in range(75):  # 3 s @ 25 fps
        vw.write(rng.integers(0, 256, (240, 320, 3), dtype=np.uint8))
    vw.release()
    yield tmp.name
    os.unlink(tmp.name)


def test_video_capacity_returns_all_presets(sample_video):
    caps = video_capacity(sample_video)
    assert len(caps) == len(VIDEO_PRESETS) >= 3
    assert {c["id"] for c in caps} == {"light", "standard", "heavy"}


def test_video_capacity_result_shape(sample_video):
    caps = video_capacity(sample_video)
    for c in caps:
        for key in (
            "id", "target_crf", "expected_ber", "technique",
            "survivability_description",
            "max_bytes_per_minute_text_message",
            "max_bytes_per_minute_text_file",
            "max_bytes_image",
            "compression_preset", "text_compression_factor",
        ):
            assert key in c, f"missing {key}"
        assert c["max_bytes_per_minute_text_message"] >= 0
        assert c["max_bytes_per_minute_text_file"] >= c["max_bytes_per_minute_text_message"]


def test_video_capacity_no_compression_does_not_apply_2_5x(sample_video):
    # NO_COMPRESSION => factor 1.0: the per-minute TEXT_FILE rate equals the
    # TEXT_MESSAGE rate (no DEFLATE gain), never the legacy 2.5x inflation.
    caps = video_capacity(sample_video, compression_preset=CompressionPreset.NO_COMPRESSION)
    for c in caps:
        assert c["compression_preset"] == "no_compression"
        assert c["text_compression_factor"] == 1.0
        assert (
            c["max_bytes_per_minute_text_file"]
            == c["max_bytes_per_minute_text_message"]
        )


def test_video_capacity_chat_preset_capacity_gte_no_compression(sample_video):
    none = {c["id"]: c for c in video_capacity(sample_video, compression_preset=CompressionPreset.NO_COMPRESSION)}
    for chat in (CompressionPreset.CHAT_STANDARD, CompressionPreset.CHAT_HD):
        chat_caps = {c["id"]: c for c in video_capacity(sample_video, compression_preset=chat)}
        for cid, c in chat_caps.items():
            assert c["text_compression_factor"] == TEXT_COMPRESSION_FACTOR_CHAT
            assert (
                c["max_bytes_per_minute_text_file"]
                >= none[cid]["max_bytes_per_minute_text_file"]
            ), f"{chat} {cid} under-caps NO_COMPRESSION"


def test_video_capacity_monotonic_by_preset(sample_video):
    caps = {c["id"]: c for c in video_capacity(sample_video)}
    assert (
        caps["light"]["max_bytes_per_minute_text_message"]
        >= caps["standard"]["max_bytes_per_minute_text_message"]
        >= caps["heavy"]["max_bytes_per_minute_text_message"]
    )
    assert (
        caps["light"]["expected_ber"]
        <= caps["standard"]["expected_ber"]
        <= caps["heavy"]["expected_ber"]
    )


def test_video_capacity_duration_hint_scales_per_minute_totals(sample_video):
    # The per-minute rate is independent of duration; image (whole-clip) grows.
    base = {c["id"]: c for c in video_capacity(sample_video)}
    longer = {c["id"]: c for c in video_capacity(sample_video, duration_sec_hint=120.0)}
    for pid in ("light", "standard", "heavy"):
        assert (
            longer[pid]["max_bytes_image"] > base[pid]["max_bytes_image"]
        )
