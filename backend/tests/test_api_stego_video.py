"""
Tests for POST /api/stego/encode and /api/stego/decode with VIDEO covers.

The video path routes through ``modules.video_stego`` (I-frame DCT-QIM + H.264
CRF re-encode). These are slower than the image-path tests, so they use a small
(2s, 320x240) synthetic cover and a short message.
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

client = TestClient(app)
ENCODE_URL = "/api/stego/encode"
DECODE_URL = "/api/stego/decode"
TEXT = "Hidden message from the video cover." * 2


@pytest.fixture(scope="module")
def video_cover_path():
    frames = []
    height, width, fps, seconds = 240, 320, 24, 2
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
    yield tmp.name
    os.unlink(tmp.name)


@pytest.fixture(scope="module")
def video_cover_bytes(video_cover_path):
    with open(video_cover_path, "rb") as fh:
        return fh.read()


def _stego_path(r):
    assert r.status_code == 200, r.text
    assert r.headers.get("content-type", "").startswith("video/mp4")
    assert b"ftyp" in r.content[:64]
    p = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    p.close()
    open(p.name, "wb").write(r.content)
    return p.name


@pytest.mark.parametrize("preset", ["light", "standard", "heavy"])
def test_video_encode_decode_roundtrip(video_cover_bytes, preset):
    with tempfile.TemporaryDirectory() as td:
        r = client.post(
            ENCODE_URL,
            data={"payload_type": "TEXT_MESSAGE", "preset": preset, "password": "s3cret", "message": TEXT},
            files={"cover": ("cover.mp4", video_cover_bytes, "video/mp4")},
        )
        stego_path = _stego_path(r)
        assert r.headers["X-Stego-CRF"]  # video path sets CRF header
        # Per-encode metrics must be surfaced for the video path.
        assert "X-Stego-PSNR" in r.headers
        assert "X-Stego-BER" in r.headers
        d = client.post(
            DECODE_URL,
            data={"password": "s3cret"},
            files={"stego": ("stego.mp4", open(stego_path, "rb"), "video/mp4")},
        )
        assert d.status_code == 200, d.text
        body = d.json()
        assert body["payload_type"] == "TEXT_MESSAGE"
        assert body["message"] == TEXT


def test_video_encode_wrong_password(video_cover_bytes):
    with tempfile.TemporaryDirectory() as td:
        r = client.post(
            ENCODE_URL,
            data={"payload_type": "TEXT_MESSAGE", "password": "right-pw", "message": TEXT},
            files={"cover": ("cover.mp4", video_cover_bytes, "video/mp4")},
        )
        stego_path = _stego_path(r)
        d = client.post(
            DECODE_URL,
            data={"password": "wrong-pw"},
            files={"stego": ("stego.mp4", open(stego_path, "rb"), "video/mp4")},
        )
        assert d.status_code == 400
        assert "recover" in d.json()["detail"].lower()


def test_video_encode_image_payload(video_cover_bytes):
    # Video DCT-QIM capacity is one bit per 8x8 block. Even a minimal
    # container (header + RS + framing) is ~230 bytes -> ~2500 bits, which the
    # 2s/320x240 module fixture (2400 blocks) cannot hold, so this test builds
    # a larger 4s cover that has room for an image payload.
    frames = []
    height, width, fps, seconds = 240, 320, 24, 4
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
    big_cover_bytes = open(tmp.name, "rb").read()
    os.unlink(tmp.name)

    # A small, compressible gradient PNG that fits the 4s cover's pool.
    img = Image.new("RGB", (64, 64))
    px = img.load()
    for x in range(64):
        for y in range(64):
            px[x, y] = (x * 3 % 256, y * 3 % 256, (x + y) % 256)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    img_bytes = buf.getvalue()
    with tempfile.TemporaryDirectory() as td:
        r = client.post(
            ENCODE_URL,
            data={"payload_type": "IMAGE", "password": "pw", "message": ""},
            files={
                "cover": ("cover.mp4", big_cover_bytes, "video/mp4"),
                "payload_image": ("secret.png", img_bytes, "image/png"),
            },
        )
        stego_path = _stego_path(r)
        d = client.post(
            DECODE_URL,
            data={"password": "pw"},
            files={"stego": ("stego.mp4", open(stego_path, "rb"), "video/mp4")},
        )
        assert d.status_code == 200, d.text
        body = d.json()
        assert body["payload_type"] == "IMAGE"
        assert body["payload_base64"]
        import base64
        assert base64.b64decode(body["payload_base64"]) == img_bytes


def test_video_decode_non_stego_400(video_cover_bytes):
    d = client.post(
        DECODE_URL,
        data={"password": ""},
        files={"stego": ("stego.mp4", video_cover_bytes, "video/mp4")},
    )
    assert d.status_code == 400
    assert "no embeddable payload" in d.json()["detail"].lower()


def test_video_encode_requires_payload(video_cover_bytes):
    r = client.post(
        ENCODE_URL,
        data={"payload_type": "TEXT_MESSAGE", "message": ""},
        files={"cover": ("cover.mp4", video_cover_bytes, "video/mp4")},
    )
    assert r.status_code == 400
    assert "requires a 'message'" in r.json()["detail"]


def test_video_encode_bad_preset_400(video_cover_bytes):
    r = client.post(
        ENCODE_URL,
        data={"payload_type": "TEXT_MESSAGE", "preset": "nope", "message": TEXT},
        files={"cover": ("cover.mp4", video_cover_bytes, "video/mp4")},
    )
    assert r.status_code == 400
    assert "preset" in r.json()["detail"].lower()


def test_video_encode_payload_too_large_400(video_cover_bytes):
    r = client.post(
        ENCODE_URL,
        data={"payload_type": "TEXT_MESSAGE", "preset": "heavy", "password": "pw", "message": TEXT * 40},
        files={"cover": ("cover.mp4", video_cover_bytes, "video/mp4")},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Dedicated video endpoints: /api/stego/video/encode + /api/stego/video/decode
# ---------------------------------------------------------------------------

V_ENCODE_URL = "/api/stego/video/encode"
V_DECODE_URL = "/api/stego/video/decode"


def test_video_endpoint_roundtrip_no_compression(video_cover_bytes):
    with tempfile.TemporaryDirectory() as td:
        r = client.post(
            V_ENCODE_URL,
            data={"payload_type": "TEXT_MESSAGE", "preset": "standard", "password": "s3cret",
                  "message": TEXT, "compress": "false"},
            files={"cover": ("cover.mp4", video_cover_bytes, "video/mp4")},
        )
        stego_path = _stego_path(r)
        assert r.headers["X-Stego-CRF"]
        d = client.post(
            V_DECODE_URL,
            data={"password": "s3cret"},
            files={"stego": ("stego.mp4", open(stego_path, "rb"), "video/mp4")},
        )
        assert d.status_code == 200, d.text
        body = d.json()
        assert body["payload_type"] == "TEXT_MESSAGE"
        assert body["message"] == TEXT


def test_video_endpoint_roundtrip_with_compression(video_cover_bytes):
    with tempfile.TemporaryDirectory() as td:
        r = client.post(
            V_ENCODE_URL,
            data={"payload_type": "TEXT_MESSAGE", "preset": "standard", "password": "s3cret",
                  "message": TEXT, "compress": "true"},
            files={"cover": ("cover.mp4", video_cover_bytes, "video/mp4")},
        )
        stego_path = _stego_path(r)
        d = client.post(
            V_DECODE_URL,
            data={"password": "s3cret"},
            files={"stego": ("stego.mp4", open(stego_path, "rb"), "video/mp4")},
        )
        assert d.status_code == 200, d.text
        assert d.json()["message"] == TEXT


def test_video_endpoint_rejects_image_cover(video_cover_bytes):
    r = client.post(
        V_ENCODE_URL,
        data={"payload_type": "TEXT_MESSAGE", "message": TEXT},
        files={"cover": ("cover.png", video_cover_bytes, "image/png")},
    )
    assert r.status_code == 400
    assert "video cover" in r.json()["detail"]


def test_video_endpoint_wrong_password(video_cover_bytes):
    with tempfile.TemporaryDirectory() as td:
        r = client.post(
            V_ENCODE_URL,
            data={"payload_type": "TEXT_MESSAGE", "password": "right", "message": TEXT},
            files={"cover": ("cover.mp4", video_cover_bytes, "video/mp4")},
        )
        stego_path = _stego_path(r)
        d = client.post(
            V_DECODE_URL,
            data={"password": "nope"},
            files={"stego": ("stego.mp4", open(stego_path, "rb"), "video/mp4")},
        )
        assert d.status_code == 400
        assert "recover" in d.json()["detail"].lower()
