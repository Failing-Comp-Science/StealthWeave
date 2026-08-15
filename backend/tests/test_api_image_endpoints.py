"""
Tests for the dedicated image stego endpoints.

    POST /api/stego/image/encode   (multipart: cover, payload, password, compress)
    POST /api/stego/image/decode   (multipart: stego, password)

Covers the lossless spatial LSB path (PNG covers and JPEG covers that
decode to pixels and download as PNG), leftover DCT-QIM JPEG extract,
the compress=True/False container flag, wrong-password handling, and the
unsupported-format / payload-matrix rejections.
"""
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app

client = TestClient(app)
ENCODE_URL = "/api/stego/image/encode"
DECODE_URL = "/api/stego/image/decode"
TEXT = "No-compression LSB round trip through the PNG cover."


@pytest.fixture(scope="module")
def png_cover_bytes():
    rng = np.random.default_rng(7)
    rgb = rng.integers(0, 256, (96, 96, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def jpeg_cover_bytes():
    # Textured gradient so leftover DCT-QIM extract tests still have carriers.
    yy, xx = np.mgrid[0:512, 0:512]
    r = 128 + 90 * np.sin(xx / 12.0) + 30 * np.sin((xx + yy) / 40.0)
    g = 128 + 80 * np.cos(yy / 10.0) + 25 * np.cos((xx - yy) / 55.0)
    b = 128 + 70 * np.sin((xx + 2 * yy) / 18.0)
    rgb = np.clip(np.stack([r, g, b], axis=-1), 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _roundtrip(cover, filename, password, text, compress):
    r = client.post(
        ENCODE_URL,
        data={
            "payload_type": "TEXT_MESSAGE",
            "password": password,
            "message": text,
            "compress": str(compress).lower(),
        },
        files={"cover": (filename, cover, "image/png" if ".png" in filename else "image/jpeg")},
    )
    assert r.status_code == 200, r.text
    d = client.post(
        DECODE_URL,
        data={"password": password},
        files={"stego": ("stego" + (" .png" if ".png" in filename else ".jpg"), r.content, "image/png" if ".png" in filename else "image/jpeg")},
    )
    assert d.status_code == 200, d.text
    return d.json()


def test_png_lsb_roundtrip_no_compression(png_cover_bytes):
    body = _encode_roundtrip(png_cover_bytes, "cover.png", "pw", TEXT, compress=False)
    assert body["payload_type"] == "TEXT_MESSAGE"
    assert body["message"] == TEXT


def test_png_lsb_roundtrip_with_compression(png_cover_bytes):
    body = _encode_roundtrip(png_cover_bytes, "cover.png", "pw", TEXT, compress=True)
    assert body["payload_type"] == "TEXT_MESSAGE"
    assert body["message"] == TEXT


def test_jpeg_lsb_roundtrip_no_compression(jpeg_cover_bytes):
    body = _encode_roundtrip(jpeg_cover_bytes, "cover.jpg", "pw", TEXT, compress=False)
    assert body["payload_type"] == "TEXT_MESSAGE"
    assert body["message"] == TEXT


def test_image_encode_reports_psnr_ssim_ber(jpeg_cover_bytes):
    r = client.post(
        ENCODE_URL,
        data={"payload_type": "TEXT_MESSAGE", "password": "pw", "message": TEXT, "compress": "false"},
        files={"cover": ("cover.jpg", jpeg_cover_bytes, "image/jpeg")},
    )
    assert r.status_code == 200, r.text
    assert r.headers.get("content-type", "").startswith("image/png")
    for header in ("X-Stego-PSNR", "X-Stego-SSIM", "X-Stego-BER"):
        assert header in r.headers, f"missing {header}"
    assert float(r.headers["X-Stego-BER"]) == 0.0
    psnr_raw = r.headers["X-Stego-PSNR"]
    assert psnr_raw == "inf" or float(psnr_raw) > 20.0
    # Container size is surfaced so the Encode result panel can show it.
    assert "X-Stego-Container-Bytes" in r.headers
    assert int(r.headers["X-Stego-Container-Bytes"]) > 0


def test_image_lsb_reports_lossless_metrics(png_cover_bytes):
    r = client.post(
        ENCODE_URL,
        data={"payload_type": "TEXT_MESSAGE", "password": "pw", "message": TEXT, "compress": "false"},
        files={"cover": ("cover.png", png_cover_bytes, "image/png")},
    )
    assert r.status_code == 200, r.text
    assert r.headers.get("X-Stego-BER") == "0.0"
    assert float(r.headers["X-Stego-PSNR"]) >= 50.0  # 1 LSB/channel over random noise
    assert int(r.headers["X-Stego-Container-Bytes"]) > 0


def test_image_encode_accepts_compression_preset(jpeg_cover_bytes):
    repetitive = ("The quick brown fox jumps over the lazy dog. " * 20)
    r = client.post(
        ENCODE_URL,
        data={
            "payload_type": "TEXT_MESSAGE",
            "password": "pw",
            "message": repetitive,
            "compress": "true",
            "compression_preset": "CHAT_STANDARD",
        },
        files={"cover": ("cover.jpg", jpeg_cover_bytes, "image/jpeg")},
    )
    assert r.status_code == 200, r.text
    assert r.headers.get("content-type", "").startswith("image/png")
    d = client.post(
        DECODE_URL,
        data={"password": "pw"},
        files={"stego": ("stego.png", r.content, "image/png")},
    )
    assert d.status_code == 200, d.text
    assert d.json()["message"] == repetitive
    assert d.json()["compressed"] is True  # CHAT_STANDARD applies DEFLATE


def test_png_text_file_roundtrip(png_cover_bytes):
    doc = b"id,name\n" + b"1,alice\n" * 200
    r = client.post(
        ENCODE_URL,
        data={"payload_type": "TEXT_FILE", "password": "pw", "compress": "false"},
        files={
            "cover": ("cover.png", png_cover_bytes, "image/png"),
            "payload_file": ("report.txt", doc, "text/plain"),
        },
    )
    assert r.status_code == 200, r.text
    d = client.post(
        DECODE_URL,
        data={"password": "pw"},
        files={"stego": ("stego.png", r.content, "image/png")},
    )
    assert d.status_code == 200, d.text
    body = d.json()
    import base64
    assert body["payload_type"] == "TEXT_FILE"
    assert body["original_filename"] == "report.txt"
    assert base64.b64decode(body["payload_base64"]) == doc


def test_wrong_password_png(png_cover_bytes):
    r = client.post(
        ENCODE_URL,
        data={"payload_type": "TEXT_MESSAGE", "password": "right", "message": TEXT, "compress": False},
        files={"cover": ("cover.png", png_cover_bytes, "image/png")},
    )
    assert r.status_code == 200, r.text
    d = client.post(
        DECODE_URL,
        data={"password": "wrong"},
        files={"stego": ("stego.png", r.content, "image/png")},
    )
    assert d.status_code == 400
    assert "wrong key" in d.json()["detail"]


def test_png_encode_rejects_text_message_without_message(png_cover_bytes):
    r = client.post(
        ENCODE_URL,
        data={"payload_type": "TEXT_MESSAGE", "message": "", "compress": "False"},
        files={"cover": ("cover.png", png_cover_bytes, "image/png")},
    )
    assert r.status_code == 400
    assert "message" in r.json()["detail"]


def _tiny_jpeg_bytes(size: int = 32) -> bytes:
    rng = np.random.default_rng(3)
    rgb = rng.integers(0, 256, (size, size, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="JPEG", quality=70)
    return buf.getvalue()


def test_png_image_payload_roundtrip(png_cover_bytes):
    # 96x96 LSB budget is tight; a 256x256 cover leaves room for a small JPEG.
    rng = np.random.default_rng(11)
    cover_rgb = rng.integers(0, 256, (256, 256, 3), dtype=np.uint8)
    cover_buf = io.BytesIO()
    Image.fromarray(cover_rgb).save(cover_buf, format="PNG")
    cover = cover_buf.getvalue()
    secret = _tiny_jpeg_bytes(32)
    r = client.post(
        ENCODE_URL,
        data={"payload_type": "IMAGE", "password": "pw", "compress": "false"},
        files={
            "cover": ("cover.png", cover, "image/png"),
            "payload_image": ("chip.jpg", secret, "image/jpeg"),
        },
    )
    assert r.status_code == 200, r.text
    d = client.post(
        DECODE_URL,
        data={"password": "pw"},
        files={"stego": ("stego.png", r.content, "image/png")},
    )
    assert d.status_code == 200, d.text
    body = d.json()
    import base64
    assert body["payload_type"] == "IMAGE"
    assert body["original_filename"] == "chip.jpg"
    assert base64.b64decode(body["payload_base64"]) == secret


def test_jpeg_image_payload_roundtrip(jpeg_cover_bytes):
    secret = _tiny_jpeg_bytes(16)
    r = client.post(
        ENCODE_URL,
        data={"payload_type": "IMAGE", "password": "pw", "compress": "false"},
        files={
            "cover": ("cover.jpg", jpeg_cover_bytes, "image/jpeg"),
            "payload_image": ("chip.jpg", secret, "image/jpeg"),
        },
    )
    assert r.status_code == 200, r.text
    assert r.headers.get("content-type", "").startswith("image/png")
    d = client.post(
        DECODE_URL,
        data={"password": "pw"},
        files={"stego": ("stego.png", r.content, "image/png")},
    )
    assert d.status_code == 200, d.text
    body = d.json()
    import base64
    assert body["payload_type"] == "IMAGE"
    assert body["original_filename"] == "chip.jpg"
    assert base64.b64decode(body["payload_base64"]) == secret


def test_png_html_warning_roundtrip(png_cover_bytes):
    html = (
        b"<!DOCTYPE html><html><body><h1>THIS IS HOW HACKERS GET YOU. "
        b"STAY SAFE.</h1></body></html>"
    )
    r = client.post(
        ENCODE_URL,
        data={"payload_type": "TEXT_FILE", "password": "pw", "compress": "false"},
        files={
            "cover": ("cover.png", png_cover_bytes, "image/png"),
            "payload_file": ("payload.html", html, "text/html"),
        },
    )
    assert r.status_code == 200, r.text
    d = client.post(
        DECODE_URL,
        data={"password": "pw"},
        files={"stego": ("stego.png", r.content, "image/png")},
    )
    assert d.status_code == 200, d.text
    body = d.json()
    import base64
    assert body["payload_type"] == "TEXT_FILE"
    assert body["original_filename"] == "payload.html"
    assert "text/html" in (body.get("mime_type") or "")
    assert base64.b64decode(body["payload_base64"]) == html


def test_png_decode_clean_400_when_not_stego(png_cover_bytes):
    r = client.post(
        DECODE_URL,
        data={"password": ""},
        files={"stego": ("plain.png", png_cover_bytes, "image/png")},
    )
    assert r.status_code == 400


def test_encode_rejects_unrelated_cover_format():
    r = client.post(
        ENCODE_URL,
        data={"payload_type": "TEXT_MESSAGE", "message": "x", "compress": "False"},
        files={"cover": ("cover.gif", b"GIF89a...", "image/gif")},
    )
    assert r.status_code == 400


def _encode_roundtrip(cover, filename, password, text, compress):
    r = client.post(
        ENCODE_URL,
        data={
            "payload_type": "TEXT_MESSAGE",
            "password": password,
            "message": text,
            "compress": str(compress).lower(),
        },
        files={"cover": (filename, cover, "image/png" if ".png" in filename else "image/jpeg")},
    )
    assert r.status_code == 200, r.text
    assert r.headers.get("content-type", "").startswith("image/png")
    d = client.post(
        DECODE_URL,
        data={"password": password},
        files={"stego": ("stego.png", r.content, "image/png")},
    )
    assert d.status_code == 200, d.text
    return d.json()


def test_legacy_dct_jpeg_still_decodes(jpeg_cover_bytes):
    # Encode no longer produces DCT JPEG, but leftover DCT-QIM files must extract.
    from modules.capacity.dct_embedder import encode_jpeg
    from modules.container import CompressionPresetId, PayloadType as CPT, build_container

    rgb = np.array(Image.open(io.BytesIO(jpeg_cover_bytes)).convert("RGB"))
    container = build_container(
        b"legacy dct",
        CPT.TEXT_MESSAGE,
        compression_preset=CompressionPresetId.LIGHT,
        password=None,
        compress=False,
        use_ecc=True,
    )
    jpeg, _ = encode_jpeg(rgb, container, quality_factor=95)
    d = client.post(
        DECODE_URL,
        data={"password": ""},
        files={"stego": ("stego.jpg", jpeg, "image/jpeg")},
    )
    assert d.status_code == 200, d.text
    assert d.json()["message"] == "legacy dct"