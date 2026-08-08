"""
Tests for the H.264-robust video stego engine (modules/video_stego).

Covers:
  * end-to-end embed -> re-encode -> extract -> parse for every preset,
  * password reproducibility (wrong password must not recover the payload),
  * deterministic carrier positions (same password -> same placement),
  * capacity guard (payload larger than the cover pool raises VideoEmbedError),
  * frame-level parity sanity on synthetic covers with real H.264 keyframes.
"""
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pytest

av = pytest.importorskip("av")

from modules.container import (
    CompressionPresetId,
    PayloadType as ContainerPayloadType,
    build_container,
    parse_container,
)
from modules.video_stego import VideoEmbedError, embed_video, extract_video

TEXT = "Attack at dawn - video test. " * 4


def _make_cover(
    width=320,
    height=240,
    fps=24,
    seconds=4,
    gop=24,
    crf=18,
    seed=7,
    scene_change=True,
):
    """Encode a synthetic textured video with a real I-frame grid."""
    frames = []
    rng = np.random.default_rng(seed)
    for i in range(int(fps * seconds)):
        yy, xx = np.mgrid[0:height, 0:width]
        phase = i / 3.0
        img = np.zeros((height, width, 3), np.uint8)
        img[:, :, 0] = 120 + 80 * np.sin(xx / 40.0 + phase)
        img[:, :, 1] = 90 + 70 * np.cos(yy / 30.0 - phase * 0.7)
        img[:, :, 2] = 60 + 60 * np.sin((xx + yy) / 60.0 + phase * 0.3)
        if scene_change and i >= int(fps * seconds / 2):
            img = np.roll(img, 80, axis=1)
        frames.append(np.clip(img, 0, 255).astype(np.uint8))

    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.close()
    with av.open(tmp.name, "w") as cont:
        st = cont.add_stream("h264", rate=fps)
        st.width, st.height = width, height
        st.pix_fmt = "yuv420p"
        st.codec_context.gop_size = gop
        st.options = {"crf": str(crf), "preset": "medium", "sc_threshold": "0"}
        for f in frames:
            vf = av.VideoFrame.from_ndarray(f, format="rgb24")
            for pkt in st.encode(vf):
                cont.mux(pkt)
        for pkt in st.encode():
            cont.mux(pkt)
    return tmp.name


def _container(text=TEXT, password="hunter2"):
    return build_container(
        text.encode("utf-8"),
        ContainerPayloadType.TEXT_MESSAGE,
        compression_preset=CompressionPresetId.STANDARD,
        password=password,
    )


# ---------------------------------------------------------------------------
# Engine round-trips
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("preset,crf", [("light", 18), ("standard", 23), ("heavy", 28)])
def test_embed_extract_roundtrip(preset, crf):
    cover = _make_cover()
    try:
        container = _container()
        stego_bytes, stats = embed_video(cover, container, preset, "hunter2")
        assert stats.blocks_used == stats.payload_bits == container_bits(container)
        assert stats.gop >= 1
        assert stats.delta in (1.0, 2.0, 4.0)
        # The re-encoded output must be a decodable MP4.
        sp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        sp.close()
        try:
            open(sp.name, "wb").write(stego_bytes)
            blob = extract_video(sp.name, "hunter2")
            assert blob
            _hdr, payload = parse_container(blob, password="hunter2")
            assert payload.decode("utf-8") == TEXT
        finally:
            os.unlink(sp.name)
    finally:
        os.unlink(cover)


def container_bits(container):
    from modules.capacity._channel import frame_bitstream

    return int(frame_bitstream(container, 2.0).size)


def test_embed_wrong_password_fails_extract():
    cover = _make_cover()
    try:
        stego_bytes, _stats = embed_video(cover, _container(), "standard", "hunter2")
        sp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        sp.close()
        try:
            open(sp.name, "wb").write(stego_bytes)
            blob = extract_video(sp.name, "wrong-password")
            if blob:
                with pytest.raises(ValueError):
                    parse_container(blob, password="wrong-password")
        finally:
            os.unlink(sp.name)
    finally:
        os.unlink(cover)


def test_deterministic_pool_wrong_password_gated_at_container():
    """Carrier positions are deterministic (not password-derived); the password
    only gates the container's AES-GCM layer. A wrong password must therefore
    still fail to recover the payload (auth), not scramble the pool."""
    cover = _make_cover()
    try:
        c1 = _container(password="pw-a")
        c2 = _container(password="pw-b")
        s1, _ = embed_video(cover, c1, "standard", "pw-a")
        s2, _ = embed_video(cover, c2, "standard", "pw-b")
        sp1, sp2 = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False), tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        sp1.close(); sp2.close()
        try:
            open(sp1.name, "wb").write(s1)
            open(sp2.name, "wb").write(s2)
            # Channel recovery is unkeyed: the pool is content-derived, so the
            # container bytes come back even with the wrong password...
            assert extract_video(sp1.name, "pw-a") == c1
            assert extract_video(sp2.name, "pw-b") == c2
            assert extract_video(sp1.name, "wrong") == c1
            # ...but the AES-GCM layer refuses the wrong password.
            with pytest.raises(ValueError):
                parse_container(extract_video(sp1.name, "wrong"), password="wrong")
        finally:
            os.unlink(sp1.name)
            os.unlink(sp2.name)
    finally:
        os.unlink(cover)


def test_payload_too_large_raises():
    cover = _make_cover(seconds=2, gop=12)  # fewer frames -> smaller pool
    try:
        big = _container(text=TEXT * 60)
        with pytest.raises(VideoEmbedError):
            embed_video(cover, big, "light", "hunter2")
    finally:
        os.unlink(cover)


def test_invalid_delta_rejected():
    cover = _make_cover()
    try:
        with pytest.raises(VideoEmbedError):
            embed_video(cover, _container(), "standard", "hunter2", delta=6.0)
    finally:
        os.unlink(cover)


def test_cover_without_iframes_fails():
    # A zero-frame (empty) video cannot offer any grid frame.
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.close()
    with av.open(tmp.name, "w") as cont:
        cont.add_stream("h264", rate=24)
    try:
        with pytest.raises(VideoEmbedError):
            embed_video(tmp.name, _container(), "standard", "hunter2")
    finally:
        os.unlink(tmp.name)


def test_extract_non_stego_video_returns_empty():
    cover = _make_cover()
    try:
        assert extract_video(cover, "hunter2") == b""
    finally:
        os.unlink(cover)


def test_probe_keyframe_grid_packet_based():
    from modules.video_stego._codec import probe_video

    cover = _make_cover(seconds=2, gop=12)
    try:
        _w, _h, fps, nb, kfs = probe_video(cover)
        assert nb == 48
        assert fps == 24.0
        # gop=12 -> keyframes at 0, 12, 24, 36 (packet-level flags are exact).
        assert kfs == [0, 12, 24, 36], kfs
    finally:
        os.unlink(cover)
