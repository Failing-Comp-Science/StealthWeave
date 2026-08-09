"""
Precedence tests for the two independent encode axes (Stage 2D):

  * `carrier_preset` (chat_standard | chat_hd | lossless_high_capacity) —
    carrier quality/derating axis.
  * `payload_compression` (NO_COMPRESSION | DEFLATE) — payload byte
    compression axis, INDEPENDENT of the carrier preset.

Locks in the resolution rules implemented in `app.api.stego`:
  1. an explicit `payload_compression` always wins over the carrier's
     `payloadCompressionDefault` (a user picking NO_COMPRESSION on a chat
     carrier must get NO_COMPRESSION),
  2. when the field is absent, an explicitly-chosen non-default carrier
     applies its own default (chat_hd -> DEFLATE),
  3. legacy clients (no new fields) keep legacy semantics: the `compress`
     flag decides, defaulting to NO_COMPRESSION.
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


def _png(w=96, h=96, seed=11):
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg(w=384, h=384, seed=12, q=95):
    # Textured gradient: high-texture 8x8 blocks everywhere, so the DCT-QIM
    # engine at QF95 (lossless carrier -> light tier) still offers enough
    # carrier bits for the test payload.
    yy, xx = np.mgrid[0:h, 0:w]
    tex = (np.sin(xx / 18.0) * np.cos(yy / 14.0) + 0.5 * np.sin((xx + yy) / 9.0)) * 40 + 128
    tex = np.clip(tex, 0, 255).astype(np.uint8)
    img = np.stack([tex, np.roll(tex, 3, 1), np.roll(tex, 5, 0)], -1)
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="JPEG", quality=q)
    return buf.getvalue()


MESSAGE = "harpocrates precedence round trip"
# Repetitive text long enough that zlib DEFLATE actually shrinks it, so the
# container sets FLAG_COMPRESSED (the container skips deflate when it does not
# help, e.g. tiny messages).
MESSAGE_COMPRESSIBLE = "The quick brown fox jumps over the lazy dog. " * 8


def _encode_decode(url, cover, filename, mime, message=MESSAGE, **data):
    r = client.post(
        url,
        data={"payload_type": "TEXT_MESSAGE", "message": message, **data},
        files={"cover": (filename, cover, mime)},
    )
    assert r.status_code == 200, r.text
    d = client.post(
        url.replace("/encode", "/decode"),
        data={"password": data.get("password", "")},
        files={"stego": ("stego", r.content, mime)},
    )
    assert d.status_code == 200, d.text
    body = d.json()
    assert body["message"] == message
    return body


# ---------------------------------------------------------------------------
# /api/stego/image/encode — explicit payload_compression wins
# ---------------------------------------------------------------------------

def test_image_explicit_no_compression_wins_over_chat_hd_default():
    # chat_hd's carrier default is DEFLATE; the explicit choice must win.
    body = _encode_decode(
        "/api/stego/image/encode", _png(), "cover.png", "image/png",
        carrier_preset="chat_hd", payload_compression="NO_COMPRESSION",
        compress="false",
    )
    assert body["compressed"] is False


def test_image_explicit_deflate_wins_over_lossless_default():
    # lossless_high_capacity's carrier default is NO_COMPRESSION; explicit
    # DEFLATE must still apply.
    body = _encode_decode(
        "/api/stego/image/encode", _png(), "cover.png", "image/png",
        message=MESSAGE_COMPRESSIBLE,
        carrier_preset="lossless_high_capacity", payload_compression="DEFLATE",
        compress="false",
    )
    assert body["compressed"] is True


def test_image_carrier_default_applies_when_field_absent():
    # No payload_compression + explicit non-default carrier -> carrier default.
    body = _encode_decode(
        "/api/stego/image/encode", _png(), "cover.png", "image/png",
        message=MESSAGE_COMPRESSIBLE,
        carrier_preset="chat_hd", compress="false",
    )
    assert body["compressed"] is True


def test_image_lossless_carrier_default_no_compression():
    body = _encode_decode(
        "/api/stego/image/encode", _png(), "cover.png", "image/png",
        carrier_preset="lossless_high_capacity",
    )
    assert body["compressed"] is False


def test_image_legacy_callers_keep_no_compression_semantics():
    # No new fields: the legacy compress flag decides (default false).
    body = _encode_decode(
        "/api/stego/image/encode", _png(), "cover.png", "image/png",
        compress="false",
    )
    assert body["compressed"] is False


def test_image_legacy_compress_flag_fallback():
    body = _encode_decode(
        "/api/stego/image/encode", _png(), "cover.png", "image/png",
        message=MESSAGE_COMPRESSIBLE, compress="true",
    )
    assert body["compressed"] is True


def test_image_jpeg_cover_carrier_preset_roundtrip():
    # JPEG cover + lossless carrier (QF95 light tier) must round-trip.
    body = _encode_decode(
        "/api/stego/image/encode", _jpeg(), "cover.jpg", "image/jpeg",
        carrier_preset="lossless_high_capacity", payload_compression="NO_COMPRESSION",
    )
    assert body["compressed"] is False


# ---------------------------------------------------------------------------
# Legacy /api/stego/encode — same precedence rules
# ---------------------------------------------------------------------------

def test_legacy_encode_explicit_no_compression_wins_over_chat_hd_default():
    body = _encode_decode(
        "/api/stego/encode", _png(), "cover.png", "image/png",
        carrier_preset="chat_hd", payload_compression="NO_COMPRESSION",
        compress="false",
    )
    assert body["compressed"] is False


def test_legacy_encode_carrier_default_applies_when_field_absent():
    body = _encode_decode(
        "/api/stego/encode", _png(), "cover.png", "image/png",
        message=MESSAGE_COMPRESSIBLE,
        carrier_preset="chat_hd", compress="false",
    )
    assert body["compressed"] is True


def test_legacy_encode_no_new_fields_is_no_compression():
    body = _encode_decode("/api/stego/encode", _png(), "cover.png", "image/png")
    assert body["compressed"] is False


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_invalid_payload_compression_422():
    r = client.post(
        "/api/stego/image/encode",
        data={"payload_type": "TEXT_MESSAGE", "message": MESSAGE,
              "payload_compression": "BZIP2"},
        files={"cover": ("cover.png", _png(), "image/png")},
    )
    assert r.status_code == 422


def test_invalid_carrier_preset_422():
    r = client.post(
        "/api/stego/image/encode",
        data={"payload_type": "TEXT_MESSAGE", "message": MESSAGE,
              "carrier_preset": "carrier_zoidberg"},
        files={"cover": ("cover.png", _png(), "image/png")},
    )
    assert r.status_code == 422
