"""
Tests for the unified preset axis at the API boundary.

The API now exposes ONE user-facing preset (``preset`` = LOSSLESS |
CHAT_STANDARD | CHAT_HD). These tests pin:

  * the capacity endpoint's ``preset`` query echo + per-row unified mapping;
  * encode round-trips under explicit unified presets;
  * precedence: an explicit unified ``preset`` beats the legacy token/axes;
  * legacy tokens (light/standard/heavy) and legacy carrier_preset values
    still work unchanged;
  * invalid unified presets surface PRESET_INVALID / 400.
"""
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

av = pytest.importorskip("av")

from app.main import app
from modules.container import TEXT_COMPRESSION_FACTOR_CHAT

client = TestClient(app)
ENCODE_URL = "/api/stego/encode"
DECODE_URL = "/api/stego/decode"
CAP_URL = "/api/stego/capacity"
TEXT = "Unified preset axis round trip." * 2
TEXT_COMPRESSIBLE = "The quick brown fox jumps over the lazy dog. " * 8


def _png(w=96, h=96, seed=11):
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg(w=384, h=384, seed=12, q=95):
    yy, xx = np.mgrid[0:h, 0:w]
    tex = (np.sin(xx / 18.0) * np.cos(yy / 14.0) + 0.5 * np.sin((xx + yy) / 9.0)) * 40 + 128
    tex = np.clip(tex, 0, 255).astype(np.uint8)
    img = np.stack([tex, np.roll(tex, 3, 1), np.roll(tex, 5, 0)], -1)
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="JPEG", quality=q)
    return buf.getvalue()


def _roundtrip_image(cover, filename, mime, **data):
    r = client.post(
        ENCODE_URL,
        data={"payload_type": "TEXT_MESSAGE", "message": TEXT, **data},
        files={"cover": (filename, cover, mime)},
    )
    assert r.status_code == 200, r.text
    d = client.post(
        DECODE_URL,
        data={},
        files={"stego": (filename, r.content, mime)},
    )
    assert d.status_code == 200, d.text
    return d.json()


# ---------------------------------------------------------------------------
# Capacity endpoint: unified preset query
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("preset", ["LOSSLESS", "CHAT_STANDARD", "CHAT_HD"])
def test_capacity_unified_preset_echoed(jpeg_bytes, preset):
    r = client.post(
        CAP_URL,
        params={"payload_type": "TEXT_MESSAGE", "preset": preset},
        files={"cover": ("cover.jpg", jpeg_bytes, "image/jpeg")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["preset"] == preset
    # JPEG covers use the spatial LSB row (same as PNG).
    by_tier = {p["id"]: p for p in body["presets"]}
    assert set(by_tier) == {"lossless_high_capacity"}
    assert by_tier["lossless_high_capacity"]["preset_id"] == "LOSSLESS"
    assert all(p["preset_label"] for p in body["presets"])


def test_capacity_lossless_is_maximum(jpeg_bytes):
    r = client.post(
        CAP_URL,
        params={"payload_type": "TEXT_MESSAGE", "preset": "LOSSLESS"},
        files={"cover": ("cover.jpg", jpeg_bytes, "image/jpeg")},
    )
    assert r.status_code == 200, r.text
    rows = r.json()["presets"]
    assert len(rows) == 1
    assert rows[0]["id"] == "lossless_high_capacity"
    assert rows[0]["max_bytes_text_message"] > 10_000


def test_capacity_pre_rename_alias_still_accepted(jpeg_bytes):
    # Old clients sending the pre-rename id LOCAL_HIGH_CAPACITY keep working and
    # are echoed under the new canonical id.
    r = client.post(
        CAP_URL,
        params={"payload_type": "TEXT_MESSAGE", "preset": "LOCAL_HIGH_CAPACITY"},
        files={"cover": ("cover.jpg", jpeg_bytes, "image/jpeg")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["preset"] == "LOSSLESS"


def test_capacity_unified_preset_changes_text_factor(jpeg_bytes):
    # CHAT_STANDARD -> the empirical DEFLATE ratio applies to TEXT_FILE rows;
    # LOSSLESS -> conservative 1.0 (exact container measured later).
    r_chat = client.post(
        CAP_URL,
        params={"payload_type": "TEXT_FILE", "preset": "CHAT_STANDARD"},
        files={"cover": ("cover.jpg", jpeg_bytes, "image/jpeg")},
    )
    r_local = client.post(
        CAP_URL,
        params={"payload_type": "TEXT_FILE", "preset": "LOSSLESS"},
        files={"cover": ("cover.jpg", jpeg_bytes, "image/jpeg")},
    )
    assert r_chat.status_code == r_local.status_code == 200
    chat_rows = r_chat.json()["presets"]
    local_rows = r_local.json()["presets"]
    for c, l in zip(chat_rows, local_rows):
        assert c["text_compression_factor"] == TEXT_COMPRESSION_FACTOR_CHAT
        assert l["text_compression_factor"] == 1.0


def test_capacity_png_spatial_row_maps_to_local(png_bytes):
    r = client.post(
        CAP_URL,
        params={"payload_type": "TEXT_MESSAGE", "preset": "CHAT_STANDARD"},
        files={"cover": ("cover.png", png_bytes, "image/png")},
    )
    assert r.status_code == 200, r.text
    (row,) = r.json()["presets"]
    assert row["id"] == "lossless_high_capacity"
    assert row["preset_id"] == "LOSSLESS"
    assert row["preset_label"] == "Lossless"


def test_capacity_legacy_compression_preset_still_wins_when_explicit(jpeg_bytes):
    # Explicit legacy compression_preset keeps legacy semantics (documented
    # compatibility) even though the unified default is LOSSLESS.
    r = client.post(
        CAP_URL,
        params={"payload_type": "TEXT_FILE", "compression_preset": "CHAT_HD"},
        files={"cover": ("cover.jpg", jpeg_bytes, "image/jpeg")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["preset"] is None
    assert body["compression_preset"] == "CHAT_HD"
    for p in body["presets"]:
        assert p["compression_preset"] == "chat_hd"


# ---------------------------------------------------------------------------
# Encode endpoint: unified preset axis + precedence
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("preset", ["LOSSLESS", "CHAT_STANDARD", "CHAT_HD"])
def test_image_roundtrip_explicit_unified_preset(preset):
    # CHAT_STANDARD (QF 75) derates carriers hardest; use a larger cover so the
    # test payload still fits all three presets.
    cover = _jpeg(640, 640) if preset == "CHAT_STANDARD" else _jpeg()
    body = _roundtrip_image(cover, "cover.jpg", "image/jpeg", preset=preset)
    assert body["message"] == TEXT


def test_unified_preset_wins_over_legacy_carrier_axis():
    # Explicit unified preset beats the legacy carrier_preset axis.
    body = _roundtrip_image(
        _jpeg(), "cover.jpg", "image/jpeg",
        preset="LOSSLESS", carrier_preset="chat_hd",
    )
    assert body["message"] == TEXT


def test_unified_preset_compression_policy(video_cover_bytes):
    # LOSSLESS -> CRF 18 + DEFLATE-if-smaller; the header echoes the
    # unified preset id.
    r = client.post(
        ENCODE_URL,
        data={"payload_type": "TEXT_MESSAGE", "message": TEXT_COMPRESSIBLE,
              "preset": "LOSSLESS"},
        files={"cover": ("cover.mp4", video_cover_bytes, "video/mp4")},
    )
    assert r.status_code == 200, r.text
    assert r.headers["X-Stego-CRF"] == "18"
    assert r.headers["X-Stego-Preset"] == "LOSSLESS"


def test_video_unified_precedence_over_legacy_carrier(video_cover_bytes):
    # Unified CHAT_STANDARD (CRF 28) must beat the legacy carrier_preset axis
    # (lossless_high_capacity -> CRF 18) sent by an old client.
    r = client.post(
        ENCODE_URL,
        data={"payload_type": "TEXT_MESSAGE", "message": TEXT,
              "preset": "CHAT_STANDARD", "carrier_preset": "lossless_high_capacity"},
        files={"cover": ("cover.mp4", video_cover_bytes, "video/mp4")},
    )
    assert r.status_code == 200, r.text
    assert r.headers["X-Stego-CRF"] == "28"
    assert r.headers["X-Stego-Preset"] == "CHAT_STANDARD"


def test_legacy_preset_tokens_still_work(video_cover_bytes):
    # Old clients sending `preset=light` get legacy semantics (CRF 18).
    r = client.post(
        ENCODE_URL,
        data={"payload_type": "TEXT_MESSAGE", "message": TEXT, "preset": "light"},
        files={"cover": ("cover.mp4", video_cover_bytes, "video/mp4")},
    )
    assert r.status_code == 200, r.text
    assert r.headers["X-Stego-CRF"] == "18"
    assert "X-Stego-Preset" not in r.headers


def test_legacy_carrier_preset_alias_still_works(jpeg_bytes):
    # The legacy carrier_preset axis is still honored when no unified preset is
    # sent (here the legacy `preset=light` token is overridden by
    # lossless_high_capacity -> QF 95 light tier).
    r = client.post(
        ENCODE_URL,
        data={"payload_type": "TEXT_MESSAGE", "message": TEXT,
              "preset": "light", "carrier_preset": "lossless_high_capacity"},
        files={"cover": ("cover.jpg", jpeg_bytes, "image/jpeg")},
    )
    assert r.status_code == 200, r.text
    assert "X-Stego-Preset" not in r.headers  # legacy axis sets no unified header
    d = client.post(
        DECODE_URL,
        data={},
        files={"stego": ("cover.jpg", r.content, "image/jpeg")},
    )
    assert d.status_code == 200, d.text
    assert d.json()["message"] == TEXT


def test_invalid_unified_preset_400(jpeg_bytes):
    # A token that is neither unified nor legacy -> PRESET_INVALID (400).
    r = client.post(
        ENCODE_URL,
        data={"payload_type": "TEXT_MESSAGE", "message": TEXT, "preset": "BOGUS"},
        files={"cover": ("cover.jpg", jpeg_bytes, "image/jpeg")},
    )
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "PRESET_INVALID"


# ---------------------------------------------------------------------------
# Fixtures (module-scoped; video encode is slow)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def video_cover_bytes():
    frames = []
    height, width, fps, seconds = 240, 320, 24, 3
    for i in range(fps * seconds):
        yy, xx = np.mgrid[0:height, 0:width]
        phase = i / 3.0
        img = np.zeros((height, width, 3), np.uint8)
        img[:, :, 0] = 120 + 80 * np.sin(xx / 40.0 + phase)
        img[:, :, 1] = 90 + 70 * np.cos(yy / 30.0 - phase * 0.7)
        img[:, :, 2] = 60 + 60 * np.sin((xx + yy) / 60.0 + phase * 0.3)
        frames.append(np.clip(img, 0, 255).astype(np.uint8))
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.close()
    with av.open(tmp.name, "w") as cont:
        st = cont.add_stream("h264", rate=fps)
        st.width, st.height = width, height
        st.pix_fmt = "yuv420p"
        st.codec_context.gop_size = 24
        st.options = {"crf": "18", "preset": "medium", "sc_threshold": "0"}
        for f in frames:
            vf = av.VideoFrame.from_ndarray(f, format="rgb24")
            for pkt in st.encode(vf):
                cont.mux(pkt)
        for pkt in st.encode():
            cont.mux(pkt)
    try:
        with open(tmp.name, "rb") as fh:
            yield fh.read()
    finally:
        os.unlink(tmp.name)


@pytest.fixture(scope="module")
def jpeg_bytes():
    return _jpeg()


@pytest.fixture(scope="module")
def png_bytes():
    return _png()
