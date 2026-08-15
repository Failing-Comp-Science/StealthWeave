"""Tests for POST /api/steganalysis/sequential-ws."""
from __future__ import annotations

import io
import os
import sys

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.main import app
from modules.image_stego import LSBEmbedder

client = TestClient(app)
URL = "/api/steganalysis/sequential-ws"


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


def test_sequential_ws_endpoint_schema_clean():
    png = _png_bytes(_natural_image())
    r = client.post(URL, files={"cover": ("cover.png", png, "image/png")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["detector"] == "sequential_ws"
    assert body["decision"] in ("clean", "suspicious", "inconclusive")
    assert body["implementation_version"] == "1.1.0"
    assert "red" in body["channel_scores"]
    assert isinstance(body["candidate_curve"], list)
    assert isinstance(body["limitations"], list)
    assert body["limitations"]
    assert "runtime_ms" in body
    if body["decision"] != "suspicious":
        assert body["detected"] is False


def test_sequential_ws_endpoint_flags_sequential_lsb():
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
    assert body["decision"] == "suspicious"
    assert body["detected"] is True
    assert body["estimated_prefix_samples"] is not None
    assert body["estimated_payload_bits"] is not None


def test_sequential_ws_endpoint_accepts_mode_and_grid():
    png = _png_bytes(_natural_image(seed=2))
    r = client.post(
        URL,
        files={"cover": ("cover.png", png, "image/png")},
        data={"mode": "prefix", "candidate_min": "256", "n_candidates": "6"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["detector"] == "sequential_ws"


def test_sequential_ws_endpoint_rejects_bad_mode():
    png = _png_bytes(_natural_image())
    r = client.post(
        URL,
        files={"cover": ("cover.png", png, "image/png")},
        data={"mode": "fft"},
    )
    assert r.status_code == 400


def test_sequential_ws_endpoint_rejects_non_image():
    r = client.post(
        URL,
        files={"cover": ("notes.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert r.status_code == 400
