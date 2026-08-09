"""Stage 2E benchmark: capacity by carrier preset + lossless round-trip check.

Representative matrix: PNG / BMP / JPEG covers + a 3s synthetic MP4, each
measured for every carrier preset the UI exposes (chat_standard, chat_hd,
lossless_high_capacity). Also verifies the lossless direct-extraction guarantee
for PNG (lossless_high_capacity carrier -> decode -> byte-exact payload).

Usage: .venv/bin/python evaluation/benchmark_carrier_presets.py
"""
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app

client = TestClient(app)

CARRIERS = ["chat_standard", "chat_hd", "lossless_high_capacity"]


def _png_bytes(h=256, w=256, seed=1):
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "PNG")
    return buf.getvalue()


def _bmp_bytes(h=256, w=256, seed=2):
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "BMP")
    return buf.getvalue()


def _jpeg_bytes(h=512, w=512, q=92):
    yy, xx = np.mgrid[0:h, 0:w]
    tex = (np.sin(xx / 18.0) * np.cos(yy / 14.0) + 0.5 * np.sin((xx + yy) / 9.0)) * 40 + 128
    tex = np.clip(tex, 0, 255).astype(np.uint8)
    img = np.stack([tex, np.roll(tex, 3, 1), np.roll(tex, 5, 0)], -1)
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, "JPEG", quality=q)
    return buf.getvalue()


def _mp4_bytes():
    import av
    frames = []
    h, w, fps, seconds = 240, 320, 24, 3
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
            vf = av.VideoFrame.from_ndarray(f, format="rgb24")
            for pkt in st.encode(vf):
                cont.mux(pkt)
        for pkt in st.encode():
            cont.mux(pkt)
    with open(tmp.name, "rb") as fh:
        data = fh.read()
    os.unlink(tmp.name)
    return data


def capacity_for(carrier_id, cover, filename, mime):
    r = client.post(
        "/api/stego/capacity",
        params={"payload_type": "TEXT_MESSAGE", "compression_preset": "NO_COMPRESSION"},
        files={"cover": (filename, cover, mime)},
    )
    assert r.status_code == 200, r.text
    presets = r.json()["presets"]
    if any(p["id"] == "lossless_high_capacity" for p in presets):
        tier_id = "lossless_high_capacity"
    elif carrier_id == "chat_standard":
        tier_id = next(p["id"] for p in presets if p["id"] == "heavy")
    elif carrier_id == "chat_hd":
        tier_id = next(p["id"] for p in presets if p["id"] == "standard")
    else:
        tier_id = next(p["id"] for p in presets if p["id"] == "light")
    p = next(p for p in presets if p["id"] == tier_id)
    # Video presets expose per-minute rates; image presets absolute bytes.
    if p.get("max_bytes_text_message") is not None:
        return p["max_bytes_text_message"], p["max_bytes_text_file"], p["expected_ber"]
    per_min = p.get("max_bytes_per_minute_text_message") or 0
    return per_min * 3, (p.get("max_bytes_per_minute_text_file") or 0) * 3, p["expected_ber"]


def lossless_roundtrip(cover, filename, mime):
    """Encode lossless_high_capacity + NO_COMPRESSION, decode, compare bytes."""
    message = "Harpocrates lossless round-trip marker " * 10  # compressible
    r = client.post(
        "/api/stego/image/encode",
        data={"payload_type": "TEXT_MESSAGE", "message": message,
              "carrier_preset": "lossless_high_capacity",
              "payload_compression": "NO_COMPRESSION"},
        files={"cover": (filename, cover, mime)},
    )
    assert r.status_code == 200, r.text
    d = client.post(
        "/api/stego/image/decode",
        data={},
        files={"stego": ("stego.png", r.content, "image/png")},
    )
    assert d.status_code == 200, d.text
    body = d.json()
    return body["message"] == message and body["compressed"] is False


def main():
    png = _png_bytes()
    bmp = _bmp_bytes()
    jpeg = _jpeg_bytes()
    mp4 = _mp4_bytes()

    print(f"{'cover':<8} {'carrier':<22} {'msg cap (B)':>12} {'file cap (B)':>13} {'ber':>8}")
    print("-" * 70)
    rows = [("PNG", png, "cover.png", "image/png"),
            ("BMP", bmp, "cover.bmp", "image/bmp"),
            ("JPEG", jpeg, "cover.jpg", "image/jpeg"),
            ("MP4", mp4, "cover.mp4", "video/mp4")]
    for name, data, fname, mime in rows:
        for c in CARRIERS:
            msg, fcap, ber = capacity_for(c, data, fname, mime)
            print(f"{name:<8} {c:<22} {msg:>12,} {fcap:>13,} {ber:>8.4f}")

    print()
    print("Lossless round-trip (PNG, lossless_high_capacity, NO_COMPRESSION):",
          "PASS" if lossless_roundtrip(png, "cover.png", "image/png") else "FAIL")
    print("Lossless round-trip (BMP, lossless_high_capacity, NO_COMPRESSION):",
          "PASS" if lossless_roundtrip(bmp, "cover.bmp", "image/bmp") else "FAIL")


if __name__ == "__main__":
    main()
