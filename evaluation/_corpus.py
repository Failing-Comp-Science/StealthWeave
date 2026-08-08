"""
Deterministic synthetic test corpus for the evaluation harness.

No external media is required: every cover and payload is generated from a
fixed seed, so the whole evaluation reproduces bit-for-bit on any machine.

Reuse (audit §6 REUSE MAP): the video cover is encoded with the same PyAV
helper pattern already used by ``backend/modules/video_stego/_codec`` and the
engine's own pytest suite, so the I-frame grid the embedder expects is real.
"""
from __future__ import annotations

import io
import os
import tempfile
import zlib
from typing import Optional, Tuple

import numpy as np
from PIL import Image, ImageFilter

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.abspath(os.path.join(_HERE, "..", "backend"))
CORPUS_DIR = os.path.join(_HERE, "test_corpus")
RESULTS_DIR = os.path.join(_HERE, "results")
SAMPLES_DIR = os.path.join(RESULTS_DIR, "samples")

DEFAULT_SEED = 20260807


def ensure_dirs() -> None:
    for d in (CORPUS_DIR, RESULTS_DIR, SAMPLES_DIR):
        os.makedirs(d, exist_ok=True)


# ---------------------------------------------------------------------------
# Cover images (RGB HxWx3 uint8, deterministic)
# ---------------------------------------------------------------------------

def _checker_gradient(size: int, rng: np.random.Generator, cell: int = 16) -> np.ndarray:
    yy, xx = np.mgrid[0:size, 0:size]
    r = 120 + 70 * np.sin(xx / 45.0) + 30 * np.sin((xx + yy) / 90.0)
    g = 95 + 75 * np.cos(yy / 38.0) + 25 * np.cos((xx - yy) / 120.0)
    b = 160 + 60 * np.sin((xx + yy) / 70.0)
    checker = ((xx // cell) + (yy // cell)) % 2 == 0
    r = np.where(checker, r + 40, r - 25)
    g = np.where(checker, g - 30, g + 35)
    noise = rng.normal(0.0, 4.0, (size, size, 3))
    return np.clip(np.stack([r, g, b], axis=-1) + noise, 0, 255).astype(np.uint8)


def _photo_like(size: int, rng: np.random.Generator) -> np.ndarray:
    """Smooth radial color wash + soft noise: natural, low-to-mid texture."""
    yy, xx = np.mgrid[0:size, 0:size]
    cx, cy = size * 0.35, size * 0.4
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    r = 30 + 200 * np.exp(-d / (size * 0.55))
    g = 60 + 170 * np.exp(-d / (size * 0.8)) + 40 * np.sin(yy / 60.0)
    b = 120 + 120 * np.exp(-d / (size * 1.1))
    noise = rng.normal(0.0, 2.0, (size, size, 3))
    return np.clip(np.stack([r, g, b], axis=-1) + noise, 0, 255).astype(np.uint8)


def _noise_field(size: int, rng: np.random.Generator) -> np.ndarray:
    """Uniform random noise: maximum texture (every block is a carrier)."""
    base = rng.integers(0, 255, (size, size, 3), dtype=np.uint8)
    blurred = np.stack(
        [Image.fromarray(base[:, :, i]).filter(ImageFilter.GaussianBlur(0.6)) for i in range(3)],
        axis=-1,
    )
    return np.clip(blurred + rng.integers(0, 24, (size, size, 3)), 0, 255).astype(np.uint8)


IMAGE_COVER_KINDS = ("photo-like", "texture-grid", "noise")


def image_cover(kind: str, size: int = 512, seed: int = DEFAULT_SEED) -> np.ndarray:
    """Return one deterministic cover image (kind in IMAGE_COVER_KINDS).

    Note: the per-kind seed uses ``zlib.crc32`` (NOT Python's ``hash()``, which
    is salted per process) so runs reproduce bit-for-bit across machines.
    """
    rng = np.random.default_rng(seed + zlib.crc32(kind.encode("utf-8")) % 10000)
    if kind == "photo-like":
        return _photo_like(size, rng)
    if kind == "texture-grid":
        return _checker_gradient(size, rng)
    if kind == "noise":
        return _noise_field(size, rng)
    raise ValueError(f"unknown cover kind '{kind}'")


def write_cover_png(rgb: np.ndarray, out_path: str) -> None:
    Image.fromarray(rgb).save(out_path, format="PNG")


# ---------------------------------------------------------------------------
# Cover video (deterministic H.264 MP4 with a real I-frame grid)
# ---------------------------------------------------------------------------

def video_cover(
    out_path: str,
    width: int = 512,
    height: int = 384,
    fps: int = 24,
    seconds: int = 3,
    gop: int = 24,
    crf: int = 18,
    seed: int = DEFAULT_SEED,
) -> str:
    """Encode a synthetic moving scene; returns ``out_path``.

    Mirrors the pattern in ``backend/tests/test_video_stego.py`` so keyframes
    land at display indices 0, gop, 2*gop, ... with ``sc_threshold=0``.
    """
    try:
        import av
    except Exception as exc:  # pragma: no cover - env guard
        raise RuntimeError("PyAV (av) is required to generate the cover video") from exc

    rng = np.random.default_rng(seed)
    nframes = int(fps * seconds)
    frames = []
    for i in range(nframes):
        yy, xx = np.mgrid[0:height, 0:width]
        phase = i / 3.0
        img = np.zeros((height, width, 3), np.uint8)
        img[:, :, 0] = 120 + 80 * np.sin(xx / 40.0 + phase)
        img[:, :, 1] = 90 + 70 * np.cos(yy / 30.0 - phase * 0.7)
        img[:, :, 2] = 60 + 60 * np.sin((xx + yy) / 60.0 + phase * 0.3)
        if i >= int(nframes / 2):
            img = np.roll(img, 64, axis=1)
        frames.append(np.clip(img, 0, 255).astype(np.uint8))

    with av.open(out_path, "w") as cont:
        st = cont.add_stream("h264", rate=fps)
        st.width, st.height = width, height
        st.pix_fmt = "yuv420p"
        st.codec_context.gop_size = gop
        st.codec_context.max_b_frames = 2
        st.options = {"crf": str(crf), "preset": "medium", "sc_threshold": "0"}
        for f in frames:
            vf = av.VideoFrame.from_ndarray(f, format="rgb24")
            for pkt in st.encode(vf):
                cont.mux(pkt)
        for pkt in st.encode():
            cont.mux(pkt)
    return out_path


# ---------------------------------------------------------------------------
# Payloads (sized to fit a cover's capacity)
# ---------------------------------------------------------------------------

_SENTENCE = (
    "The quick brown fox jumps over the lazy dog. Harpocrates steganography "
    "evaluation payload. "
)

_FILE_BODY = (
    "HARPOCRATES EVALUATION FILE\n"
    "==========================\n"
    "Deterministic text-file payload used by the benchmark harness. It repeats "
    "a paragraph of mostly-compressible prose so the TEXT_FILE container "
    "exercises the DEFLATE stage, and is regenerated from a fixed seed so the "
    "evaluation is reproducible.\n\n"
) * 8


def make_text_payload(max_bytes: int, kind: str = "message") -> bytes:
    """Deterministic text payload of at most ``max_bytes`` bytes.

    kind='message' -> a short message; kind='file' -> structured file text.
    """
    max_bytes = max(0, int(max_bytes))
    if max_bytes == 0:
        return b""
    if kind == "file":
        body = _FILE_BODY
        if len(body) > max_bytes:
            # Trim to a whole repeated unit boundary approximation.
            unit = _FILE_BODY.split("\n\n", 1)[0] + "\n\n"
            reps = max_bytes // len(unit)
            body = unit * max(1, reps) if reps else unit[:max_bytes]
        return body.encode("utf-8")[:max_bytes]
    unit = _SENTENCE
    reps = max_bytes // len(unit)
    return (unit * max(1, reps))[:max_bytes].encode("utf-8")


def make_image_payload(max_bytes: int, seed: int = DEFAULT_SEED) -> bytes:
    """Smallest-possible deterministic PNG payload that fits ``max_bytes``.

    Starts from a solid-color block and shrinks the tile until the PNG bytes
    fit the cover's capacity (a hard upper bound for IMAGE payloads).
    """
    rng = np.random.default_rng(seed)
    for tile in (4, 2, 1):
        if tile == 1:
            arr = np.full((2, 2, 3), int(rng.integers(0, 255)), np.uint8)
        else:
            arr = rng.integers(0, 256, (tile, tile, 3), dtype=np.uint8)
        buf = io.BytesIO()
        Image.fromarray(arr).save(buf, format="PNG")
        data = buf.getvalue()
        if max_bytes <= 0 or len(data) <= max_bytes:
            return data
    return b""


# ---------------------------------------------------------------------------
# Reproducibility helper
# ---------------------------------------------------------------------------

def patch_crypto_deterministic() -> None:
    """Make ``modules.crypto_utils.SteganoCrypto.encrypt_payload`` deterministic.

    ``build_container`` encrypts the container with AES-256-GCM using a random
    salt + nonce (``os.urandom``), so the same payload/password produces
    different container bytes on every call. That is correct cryptography but
    makes an evaluation harness non-reproducible. This patch derives the salt
    and nonce from SHA-256(password || plaintext) so identical inputs yield
    identical containers -- benchmark-only; production crypto is untouched.
    """
    import hashlib

    from modules.crypto_utils import SteganoCrypto

    def _deterministic_encrypt(plaintext: bytes, password: str) -> bytes:
        digest = hashlib.sha256(password.encode("utf-8") + bytes(plaintext)).digest()
        salt = digest[:SteganoCrypto.SALT_SIZE]
        nonce = hashlib.sha256(digest[::-1]).digest()[: SteganoCrypto.NONCE_SIZE]
        key = SteganoCrypto.derive_key(password, salt)
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        ciphertext_with_tag = AESGCM(key).encrypt(nonce, bytes(plaintext), None)
        return salt + nonce + ciphertext_with_tag

    SteganoCrypto.encrypt_payload = staticmethod(_deterministic_encrypt)
