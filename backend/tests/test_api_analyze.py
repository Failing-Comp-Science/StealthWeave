"""Tests for POST /api/stego/analyze (chi-square + SPA + RS steganalysis)."""
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from modules.image_stego import LSBEmbedder

client = TestClient(app)
URL = "/api/stego/analyze"


def _png_bytes(rgb: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="PNG")
    return buf.getvalue()


def _natural_image(seed=0, size=256):
    rng = np.random.RandomState(seed)
    x = np.linspace(0, 1, size)
    y = np.linspace(0, 1, size)
    xx, yy = np.meshgrid(x, y)
    base = 128 + 60 * np.sin(3 * xx) * np.cos(2 * yy)
    img = np.stack([base, base * 0.9, base * 1.1], axis=-1)
    img += rng.randn(size, size, 3) * 3
    return np.clip(img, 0, 255).astype(np.uint8)


def test_analyze_clean_image_schema():
    png = _png_bytes(_natural_image())
    r = client.post(URL, files={"cover": ("cover.png", png, "image/png")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] in ("likely_clean", "lsb_suspected")
    assert "detected" in body["chi_square"]
    assert "stego_probability" in body["chi_square"]
    assert "chi2_stat" in body["chi_square"]
    assert "prefix_detected" in body["chi_square"]
    assert "detected" in body["sample_pairs"]
    assert "estimated_payload" in body["sample_pairs"]
    assert "detected" in body["rs_analysis"]
    assert "estimated_payload" in body["rs_analysis"]
    assert "detected" in body["primary_sets"]
    assert "estimated_payload" in body["primary_sets"]
    ws = body["sequential_ws"]
    assert ws["detector"] == "sequential_ws"
    assert ws["decision"] in ("clean", "suspicious", "inconclusive")
    assert "detected" in ws
    assert "candidate_curve" in ws
    assert "channel_scores" in ws
    header = body["hstg_header"]
    assert "found" in header
    assert header["found"] is False


def test_analyze_clean_natural_png_likely_clean():
    png = _png_bytes(_natural_image(seed=0))
    r = client.post(URL, files={"cover": ("cover.png", png, "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == "likely_clean"


def test_analyze_textured_cover_likely_clean():
    rng = np.random.RandomState(1)
    yy, xx = np.mgrid[0:256, 0:256]
    rch = 120 + 40 * np.sin(xx / 9.0) + rng.randn(256, 256) * 8
    gch = 90 + 50 * np.cos(yy / 7.0) + rng.randn(256, 256) * 8
    bch = 140 + 35 * np.sin((xx + yy) / 11.0) + rng.randn(256, 256) * 8
    img = np.clip(np.stack([rch, gch, bch], axis=-1), 0, 255).astype(np.uint8)
    r = client.post(URL, files={"cover": ("tex.png", _png_bytes(img), "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == "likely_clean"


def test_analyze_flat_cover_likely_clean():
    img = np.full((256, 256, 3), 128, dtype=np.uint8)
    r = client.post(URL, files={"cover": ("flat.png", _png_bytes(img), "image/png")})
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == "likely_clean"


def test_analyze_jpeg_cover_likely_clean():
    rgb = _natural_image(seed=2)
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="JPEG", quality=50)
    r = client.post(
        URL, files={"cover": ("photo.jpg", buf.getvalue(), "image/jpeg")}
    )
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == "likely_clean"


def test_analyze_sequential_lsb_is_suspected():
    # Modest payload (not a stuffed image) — sequential LSB on a raster prefix.
    cover = _natural_image(seed=1)
    payload = os.urandom(2500)
    stego = LSBEmbedder(random_order=False, bits_per_channel=1).embed(
        cover, payload, "key"
    ).stego_media
    r = client.post(
        URL, files={"cover": ("stego.png", _png_bytes(stego), "image/png")}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "lsb_suspected"
    assert (
        body["hstg_header"]["found"]
        or body["sample_pairs"]["detected"]
        or body["rs_analysis"]["detected"]
        or body["primary_sets"]["detected"]
        or body["sequential_ws"]["detected"]
    )
    assert body["hstg_header"]["found"] is True
    assert body["hstg_header"]["bits_per_channel"] == 1


def test_analyze_short_text_message_is_suspected():
    """Typed-message size (~11 B) misses WS on texture; the HSTG header still votes."""
    from modules.container import CompressionPresetId, PayloadType, build_container

    rng = np.random.RandomState(1)
    yy, xx = np.mgrid[0:256, 0:256]
    rch = 120 + 40 * np.sin(xx / 9.0) + rng.randn(256, 256) * 8
    gch = 90 + 50 * np.cos(yy / 7.0) + rng.randn(256, 256) * 8
    bch = 140 + 35 * np.sin((xx + yy) / 11.0) + rng.randn(256, 256) * 8
    cover = np.clip(np.stack([rch, gch, bch], axis=-1), 0, 255).astype(np.uint8)
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
    r = client.post(
        URL, files={"cover": ("stego.png", _png_bytes(stego), "image/png")}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "lsb_suspected"
    assert body["hstg_header"]["found"] is True


def test_analyze_rejects_non_image():
    r = client.post(
        URL,
        files={"cover": ("notes.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert r.status_code == 400
