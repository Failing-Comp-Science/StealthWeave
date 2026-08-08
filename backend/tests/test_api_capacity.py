"""
Tests for POST /api/stego/capacity (app/api/stego.py).

Exercises the restricted cover/payload matrix at the API boundary:
  * valid combinations for IMAGE and VIDEO covers,
  * the two required rejections (IMAGE payload into IMAGE cover; VIDEO payload
    into VIDEO cover) returning HTTP 400 with a clear message,
  * >=3 compression presets returned per cover type in a single call.
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

from app.main import app
from modules.container import TEXT_COMPRESSION_FACTOR_CHAT

client = TestClient(app)
URL = "/api/stego/capacity"


# --------------------------------------------------------------------------
# Fixtures: a real PNG and a real MP4 in-memory / on-disk
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def png_bytes():
    rng = np.random.default_rng(0)
    arr = rng.integers(0, 256, (256, 256, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def mp4_bytes():
    cv2 = pytest.importorskip("cv2")
    rng = np.random.default_rng(1)
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.close()
    vw = cv2.VideoWriter(tmp.name, cv2.VideoWriter_fourcc(*"mp4v"), 25.0, (320, 240))
    assert vw.isOpened()
    for _ in range(50):
        vw.write(rng.integers(0, 256, (240, 320, 3), dtype=np.uint8))
    vw.release()
    data = open(tmp.name, "rb").read()
    os.unlink(tmp.name)
    return data


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------

def test_healthz():
    r = client.get("/api/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# --------------------------------------------------------------------------
# Valid combinations
# --------------------------------------------------------------------------

@pytest.mark.parametrize("payload_type", ["TEXT_MESSAGE", "TEXT_FILE"])
def test_image_cover_valid_payloads(png_bytes, payload_type):
    r = client.post(
        URL, params={"payload_type": payload_type},
        files={"cover": ("cover.png", png_bytes, "image/png")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cover_type"] == "IMAGE"
    assert body["payload_type"] == payload_type
    assert body["container_version"] == 2
    assert body["compression_preset"] == "NO_COMPRESSION"
    # >=3 presets in a single call (task step 4/5).
    assert len(body["presets"]) >= 3
    assert {p["id"] for p in body["presets"]} == {"light", "standard", "heavy"}
    for p in body["presets"]:
        assert p["target_quality_factor"] is not None
        assert p["max_bytes_text_message"] is not None
        assert "expected_ber" in p and "technique" in p
        assert p["compression_preset"] == "no_compression"
        assert p["text_compression_factor"] == 1.0


@pytest.mark.parametrize("preset", ["CHAT_STANDARD", "CHAT_HD", "NO_COMPRESSION"])
def test_capacity_compression_preset_query_echoed(png_bytes, preset):
    r = client.post(
        URL,
        params={"payload_type": "TEXT_FILE", "compression_preset": preset},
        files={"cover": ("cover.png", png_bytes, "image/png")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["compression_preset"] == preset
    expected_factor = 1.0 if preset == "NO_COMPRESSION" else TEXT_COMPRESSION_FACTOR_CHAT
    for p in body["presets"]:
        assert p["compression_preset"] == preset.lower()
        assert p["text_compression_factor"] == expected_factor


def test_capacity_unknown_compression_preset_422(png_bytes):
    # Only the three known channel presets are accepted; anything else 422s.
    r = client.post(
        URL,
        params={"payload_type": "TEXT_FILE", "compression_preset": "WHATEVER"},
        files={"cover": ("cover.png", png_bytes, "image/png")},
    )
    assert r.status_code == 422


@pytest.mark.parametrize("payload_type", ["TEXT_MESSAGE", "TEXT_FILE", "IMAGE"])
def test_video_cover_valid_payloads(mp4_bytes, payload_type):
    r = client.post(
        URL, params={"payload_type": payload_type},
        files={"cover": ("cover.mp4", mp4_bytes, "video/mp4")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cover_type"] == "VIDEO"
    assert body["payload_type"] == payload_type
    assert len(body["presets"]) >= 3
    for p in body["presets"]:
        assert p["target_crf"] is not None
        assert p["max_bytes_per_minute_text_message"] is not None
        assert p["max_bytes_image"] is not None


def test_allowed_payload_types_reported(png_bytes, mp4_bytes):
    r_img = client.post(
        URL, params={"payload_type": "TEXT_MESSAGE"},
        files={"cover": ("c.png", png_bytes, "image/png")},
    )
    assert r_img.json()["allowed_payload_types"] == ["TEXT_MESSAGE", "TEXT_FILE"]

    r_vid = client.post(
        URL, params={"payload_type": "IMAGE"},
        files={"cover": ("c.mp4", mp4_bytes, "video/mp4")},
    )
    assert r_vid.json()["allowed_payload_types"] == ["TEXT_MESSAGE", "TEXT_FILE", "IMAGE"]


# --------------------------------------------------------------------------
# Required rejections (task step 5)
# --------------------------------------------------------------------------

def test_reject_image_payload_into_image_cover(png_bytes):
    r = client.post(
        URL, params={"payload_type": "IMAGE"},
        files={"cover": ("cover.png", png_bytes, "image/png")},
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "IMAGE" in detail and "not allowed" in detail
    assert "TEXT_MESSAGE" in detail and "TEXT_FILE" in detail


def test_reject_video_payload_into_image_cover(png_bytes):
    r = client.post(
        URL, params={"payload_type": "VIDEO"},
        files={"cover": ("cover.png", png_bytes, "image/png")},
    )
    assert r.status_code == 400
    assert "not allowed" in r.json()["detail"]


def test_reject_video_payload_into_video_cover(mp4_bytes):
    r = client.post(
        URL, params={"payload_type": "VIDEO"},
        files={"cover": ("cover.mp4", mp4_bytes, "video/mp4")},
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "VIDEO" in detail and "not allowed" in detail


# --------------------------------------------------------------------------
# Input hardening
# --------------------------------------------------------------------------

def test_unknown_payload_type_400(png_bytes):
    r = client.post(
        URL, params={"payload_type": "AUDIO"},
        files={"cover": ("cover.png", png_bytes, "image/png")},
    )
    assert r.status_code == 400
    assert "Unknown payload_type" in r.json()["detail"]


def test_unsupported_cover_type_400():
    r = client.post(
        URL, params={"payload_type": "TEXT_MESSAGE"},
        files={"cover": ("notes.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert r.status_code == 400
    assert "Unsupported cover type" in r.json()["detail"]


def test_corrupt_image_400():
    r = client.post(
        URL, params={"payload_type": "TEXT_MESSAGE"},
        files={"cover": ("cover.png", b"not a real png", "image/png")},
    )
    assert r.status_code == 400
    assert "decode image" in r.json()["detail"]


def test_missing_payload_type_422(png_bytes):
    # payload_type is a required query param -> FastAPI validation 422.
    r = client.post(URL, files={"cover": ("cover.png", png_bytes, "image/png")})
    assert r.status_code == 422


def test_payload_type_case_insensitive(png_bytes):
    r = client.post(
        URL, params={"payload_type": "text_message"},
        files={"cover": ("cover.png", png_bytes, "image/png")},
    )
    assert r.status_code == 200
    assert r.json()["payload_type"] == "TEXT_MESSAGE"
