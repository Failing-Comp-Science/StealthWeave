"""
Tests for structured error codes, the exact pre-embed fit check, failure
isolation (no shared state across requests), temp-file cleanup, and the media
health endpoint (Stage 1C of the runtime-reliability fix).

These lock in the runtime-reliability guarantees:
  * every stego failure returns a stable ``code`` (ErrorResponse.code),
  * a capacity-exceeded payload is rejected BEFORE any output is produced,
  * a failed video request cannot poison a subsequent image/video request
    (the backend is stateless),
  * temp files are cleaned up on both success and failure,
  * the media health endpoint reports the PyAV stack + collision guard.
"""
import glob
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


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

def _png(w=96, h=96, seed=3):
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg(w=128, h=128, seed=4, q=90):
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="JPEG", quality=q)
    return buf.getvalue()


@pytest.fixture(scope="module")
def mp4_bytes():
    frames = []
    h, w, fps, seconds = 240, 320, 24, 2
    for i in range(fps * seconds):
        yy, xx = np.mgrid[0:h, 0:w]
        phase = i / 3.0
        img = np.zeros((h, w, 3), np.uint8)
        img[:, :, 0] = 120 + 80 * np.sin(xx / 40.0 + phase)
        img[:, :, 1] = 90 + 70 * np.cos(yy / 30.0 - phase * 0.7)
        img[:, :, 2] = 60 + 60 * np.sin((xx + yy) / 60.0 + phase * 0.3)
        frames.append(np.clip(img, 0, 255).astype(np.uint8))
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.close()
    with av.open(tmp.name, "w") as cont:
        st = cont.add_stream("h264", rate=fps)
        st.width, st.height = w, h
        st.pix_fmt = "yuv420p"
        st.codec_context.gop_size = 24
        st.options = {"crf": "18", "preset": "medium", "sc_threshold": "0"}
        for f in frames:
            for pkt in st.encode(av.VideoFrame.from_ndarray(f, format="rgb24")):
                cont.mux(pkt)
        for pkt in st.encode():
            cont.mux(pkt)
    data = open(tmp.name, "rb").read()
    os.unlink(tmp.name)
    return data


def _tmp_count():
    """Count leftover Harpocrates temp files in the tmp dir (best effort)."""
    return len(glob.glob(os.path.join(tempfile.gettempdir(), "tmp*.mp4")))


# --------------------------------------------------------------------------
# Media health endpoint
# --------------------------------------------------------------------------

def test_media_health_endpoint_reports_pyav_and_collision_guard():
    r = client.get("/api/healthz/media")
    assert r.status_code == 200
    media = r.json()["media"]
    assert media["pyav_available"] is True
    assert media["h264_encoder"] is True
    assert media["ok"] is True


def test_app_import_does_not_eagerly_load_cv2():
    """The collision guard: importing the app must NOT import cv2.

    Checked in a CLEAN subprocess because other tests in this process legitimately
    use cv2 as a fixture to synthesize MP4s (which would pollute a same-process
    ``sys.modules`` check). The app's video path uses PyAV exclusively; cv2 is a
    lazy fallback prober only (see modules.capacity.video_capacity._require_cv2).
    """
    import subprocess

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = (
        "import sys; import app.main; "
        "print('cv2' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=backend_dir, capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("False"), out.stdout
    # And no dual-libavdevice objc collision warning on import.
    assert "implemented in both" not in out.stderr


def test_liveness_contract_unchanged():
    r = client.get("/api/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# --------------------------------------------------------------------------
# Structured error codes
# --------------------------------------------------------------------------

def test_video_capacity_exceeded_has_code(mp4_bytes):
    # A large incompressible payload cannot fit the tiny 2s cover.
    r = client.post(
        "/api/stego/video/encode",
        data={"payload_type": "TEXT_MESSAGE", "preset": "standard",
              "message": os.urandom(3000).hex(), "compress": "false"},
        files={"cover": ("cover.mp4", mp4_bytes, "video/mp4")},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "VIDEO_CAPACITY_EXCEEDED"
    # Fail-before-output: the response is JSON, not a video.
    assert r.headers["content-type"].startswith("application/json")


def test_image_capacity_exceeded_has_code():
    # A small noisy JPEG cannot hold a large message.
    jpeg = _jpeg(64, 64)
    r = client.post(
        "/api/stego/image/encode",
        data={"payload_type": "TEXT_MESSAGE", "preset": "standard",
              "message": "A" * 4000, "compress": "false"},
        files={"cover": ("cover.jpg", jpeg, "image/jpeg")},
    )
    assert r.status_code == 400
    assert r.json()["code"] == "IMAGE_CAPACITY_EXCEEDED"


def test_unsupported_cover_type_code():
    r = client.post(
        "/api/stego/capacity", params={"payload_type": "TEXT_MESSAGE"},
        files={"cover": ("notes.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert r.status_code == 400
    assert r.json()["code"] == "COVER_TYPE_UNSUPPORTED"


def test_empty_cover_code():
    r = client.post(
        "/api/stego/image/encode",
        data={"payload_type": "TEXT_MESSAGE", "message": "hi"},
        files={"cover": ("cover.png", b"", "image/png")},
    )
    assert r.status_code == 400
    assert r.json()["code"] == "IMAGE_FILE_EMPTY"


def test_missing_message_code():
    r = client.post(
        "/api/stego/image/encode",
        data={"payload_type": "TEXT_MESSAGE", "message": ""},
        files={"cover": ("cover.png", _png(), "image/png")},
    )
    assert r.status_code == 400
    assert r.json()["code"] == "PAYLOAD_MISSING"


def test_unsupported_image_format_code():
    r = client.post(
        "/api/stego/image/encode",
        data={"payload_type": "TEXT_MESSAGE", "message": "hi"},
        files={"cover": ("cover.gif", b"GIF89a\x00", "image/gif")},
    )
    assert r.status_code == 400
    # GIF is neither PNG/BMP nor JPEG -> IMAGE_FORMAT_UNSUPPORTED (or decode fail).
    assert r.json()["code"] in {"IMAGE_FORMAT_UNSUPPORTED", "IMAGE_DECODE_FAILED"}


def test_bad_preset_code():
    r = client.post(
        "/api/stego/image/encode",
        data={"payload_type": "TEXT_MESSAGE", "message": "hi", "preset": "nope"},
        files={"cover": ("cover.jpg", _jpeg(), "image/jpeg")},
    )
    assert r.status_code == 400
    assert r.json()["code"] == "PRESET_INVALID"


def test_corrupt_video_probe_code():
    r = client.post(
        "/api/stego/video/encode",
        data={"payload_type": "TEXT_MESSAGE", "message": "hi"},
        files={"cover": ("cover.mp4", b"not a real mp4 file at all", "video/mp4")},
    )
    assert r.status_code == 400
    assert r.json()["code"] in {"VIDEO_PROBE_FAILED", "VIDEO_NO_USABLE_FRAMES", "VIDEO_NO_I_FRAMES"}


def test_corrupt_video_capacity_probe_code():
    r = client.post(
        "/api/stego/capacity", params={"payload_type": "TEXT_MESSAGE"},
        files={"cover": ("cover.mp4", b"still not a real mp4", "video/mp4")},
    )
    assert r.status_code == 400
    assert r.json()["code"] in {"VIDEO_PROBE_FAILED", "VIDEO_NO_USABLE_FRAMES", "VIDEO_NO_I_FRAMES"}


# --------------------------------------------------------------------------
# Failure isolation: a failed video request cannot poison the next request
# --------------------------------------------------------------------------

def test_failed_video_then_png_succeeds(mp4_bytes):
    # 1) force a video failure (oversized payload)
    r_fail = client.post(
        "/api/stego/video/encode",
        data={"payload_type": "TEXT_MESSAGE", "message": os.urandom(3000).hex()},
        files={"cover": ("cover.mp4", mp4_bytes, "video/mp4")},
    )
    assert r_fail.status_code == 400
    # 2) immediately encode a PNG — must succeed (no shared state)
    r_png = client.post(
        "/api/stego/image/encode",
        data={"payload_type": "TEXT_MESSAGE", "message": "hello", "password": "pw"},
        files={"cover": ("after.png", _png(), "image/png")},
    )
    assert r_png.status_code == 200, r_png.text
    assert r_png.headers["content-type"].startswith("image/png")


def test_failed_video_then_video_succeeds(mp4_bytes):
    r_fail = client.post(
        "/api/stego/video/encode",
        data={"payload_type": "TEXT_MESSAGE", "message": os.urandom(3000).hex()},
        files={"cover": ("cover.mp4", mp4_bytes, "video/mp4")},
    )
    assert r_fail.status_code == 400
    r_ok = client.post(
        "/api/stego/video/encode",
        data={"payload_type": "TEXT_MESSAGE", "message": "short", "password": "pw"},
        files={"cover": ("cover2.mp4", mp4_bytes, "video/mp4")},
    )
    assert r_ok.status_code == 200, r_ok.text
    assert r_ok.headers["content-type"].startswith("video/mp4")


def test_failed_image_then_video_succeeds(mp4_bytes):
    r_fail = client.post(
        "/api/stego/image/encode",
        data={"payload_type": "TEXT_MESSAGE", "message": "A" * 4000, "preset": "heavy"},
        files={"cover": ("cover.jpg", _jpeg(64, 64), "image/jpeg")},
    )
    assert r_fail.status_code == 400
    r_ok = client.post(
        "/api/stego/video/encode",
        data={"payload_type": "TEXT_MESSAGE", "message": "short"},
        files={"cover": ("cover.mp4", mp4_bytes, "video/mp4")},
    )
    assert r_ok.status_code == 200, r_ok.text


# --------------------------------------------------------------------------
# Temp-file cleanup on success and failure
# --------------------------------------------------------------------------

def test_tempfiles_cleaned_on_video_failure(mp4_bytes):
    before = _tmp_count()
    client.post(
        "/api/stego/video/encode",
        data={"payload_type": "TEXT_MESSAGE", "message": os.urandom(3000).hex()},
        files={"cover": ("cover.mp4", mp4_bytes, "video/mp4")},
    )
    after = _tmp_count()
    assert after <= before  # no leaked .mp4 temp files


def test_tempfiles_cleaned_on_video_success(mp4_bytes):
    before = _tmp_count()
    r = client.post(
        "/api/stego/video/encode",
        data={"payload_type": "TEXT_MESSAGE", "message": "short"},
        files={"cover": ("cover.mp4", mp4_bytes, "video/mp4")},
    )
    assert r.status_code == 200
    after = _tmp_count()
    assert after <= before
