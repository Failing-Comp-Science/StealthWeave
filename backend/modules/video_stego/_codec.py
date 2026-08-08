"""
PyAV wrapper for the video stego engine.

Patterns borrowed from the MIT reference ``references/videoseal``
(``videoseal/augmentation/video.py``, ``inference_streaming.py``): PyAV
gives frame-level keyframe flags and in-process libx264 encoding, so we can
identify I-frames and re-encode at a preset CRF with a forced, deterministic
GOP without shelling out to an ffmpeg binary.

Responsibilities:
    * ``probe_video``        -> (width, height, fps, nb_frames, keyframe_idx)
    * ``decode_rgb``         -> iterator of (display_index, rgb24 ndarray, is_keyframe)
    * ``encode_video``       -> transcode decoded frames to libx264 at ``crf``
    * ``keyframe_grid``      -> GOP G derived from the cover's own keyframe spacing
    * ``frame_psnr``         -> per-frame PSNR helper for the benchmark/API

Re-encode invariants (must hold for embed/extract alignment):
    * ``codec_context.gop_size = G``      (keyframe every G frames)
    * ``codec_context.max_b_frames = 2``  (deterministic B-frame structure)
    * ``options['sc_threshold'] = '0'``   (suppress scene-cut I-frames)
  so the output video has I-frames exactly at display indices 0, G, 2G, ...
"""
from __future__ import annotations

import subprocess
import tempfile
from fractions import Fraction
from typing import Iterator, List, Optional, Sequence, Tuple

import numpy as np

try:
    import av
except Exception:  # pragma: no cover - environment guard (matches video_capacity)
    av = None

#: Default target GOP seconds when the cover exposes no keyframe structure.
DEFAULT_GOP_SECONDS = 2.0
#: Preset used for every libx264 pass (deterministic output).
X264_PRESET = "medium"
#: Deterministic B-frame count so re-encodes are reproducible.
MAX_B_FRAMES = 2

VideoProbeResult = Tuple[int, int, float, int, List[int]]


def _as_rational(fps: float) -> Fraction:
    """Convert a float framerate to an exact rational (PyAV requires one)."""
    return Fraction(fps).limit_denominator(1000)


class CodecUnavailableError(RuntimeError):
    """Raised when PyAV (or its bundled ffmpeg) is not importable."""


def _require_av():
    if av is None:
        raise CodecUnavailableError(
            "PyAV (av) is not available; install it with `pip install av`."
        )
    return av


# ---------------------------------------------------------------------------
# Probing / decoding
# ---------------------------------------------------------------------------

def probe_video(path: str) -> VideoProbeResult:
    """Return (width, height, fps, nb_frames, keyframe_indices) for ``path``.

    Keyframe indices are display-order positions of I-frames, read directly
    from the decoded ``frame.key_frame`` flag (PyAV).
    """
    av = _require_av()
    try:
        container = av.open(path)
    except (av.error.FFmpegError, av.error.InvalidDataError) as exc:
        raise ValueError(
            f"Cannot open video '{path}' for probing: {exc}"
        ) from exc
    try:
        stream = container.streams.video[0]
        width, height = stream.codec_context.width, stream.codec_context.height
        fps = float(stream.average_rate)
        if not fps or fps <= 0:
            try:
                fps = float(stream.codec_context.framerate)
            except (TypeError, ValueError, ZeroDivisionError):
                fps = 25.0
        keyframes: List[int] = []
        idx = 0
        for frame in container.decode(video=0):
            if frame.key_frame:
                keyframes.append(idx)
            idx += 1
        nb_frames = idx
        return width, height, fps, nb_frames, keyframes
    finally:
        container.close()


def decode_rgb(path: str) -> Iterator[Tuple[int, np.ndarray, bool]]:
    """Yield (display_index, rgb24 ndarray HxWx3, is_keyframe) for every frame."""
    av = _require_av()
    container = av.open(path)
    try:
        idx = 0
        for frame in container.decode(video=0):
            yield idx, frame.to_ndarray(format="rgb24"), bool(frame.key_frame)
            idx += 1
    finally:
        container.close()


def decode_frame(path: str, index: int) -> Optional[np.ndarray]:
    """Return the rgb24 array of frame ``index`` (None when out of range)."""
    av = _require_av()
    container = av.open(path)
    try:
        container.seek(index, any_frame=False)
    except (av.error.FFmpegError, av.error.InvalidDataError):  # pragma: no cover
        pass
    try:
        for frame in container.decode(video=0):
            if frame.index == index:
                return frame.to_ndarray(format="rgb24")
        return None
    finally:
        container.close()


# ---------------------------------------------------------------------------
# Keyframe grid
# ---------------------------------------------------------------------------

def keyframe_grid(path: str, fps: float = 0.0) -> int:
    """GOP ``G`` for the re-encode: the cover's own median keyframe spacing.

    Clamped to [1, 300] frames. When the cover exposes no keyframe structure
    (e.g. a single-IDR file) fall back to ``DEFAULT_GOP_SECONDS * fps``.
    Deterministic from the cover alone, so extraction needs no extra metadata.
    """
    _width, _height, _fps, _nb, keyframes = probe_video(path)
    fps = fps or _fps or 25.0
    if len(keyframes) >= 2:
        gaps = [
            b - a for a, b in zip(keyframes[:-1], keyframes[1:]) if b - a > 0
        ]
        if gaps:
            import statistics

            return int(min(300, max(1, int(statistics.median(gaps)))))
    return int(min(300, max(1, round(DEFAULT_GOP_SECONDS * fps))))


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

def encode_video(
    frames: Sequence[np.ndarray],
    out_path: str,
    crf: int,
    gop: int,
    fps: float = 25.0,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> None:
    """Transcode an RGB frame sequence to H.264 at ``crf`` with forced GOP.

    ``frames[i]`` is an HxWx3 uint8 ndarray; the (H, W) must be uniform.
    The output is yuv420p libx264. B-frame count is fixed for determinism.
    """
    av = _require_av()
    if not frames:
        raise ValueError("encode_video requires at least one frame")
    h, w = frames[0].shape[:2]
    if width is None:
        width = w
    if height is None:
        height = h
    if (w, h) != (width, height):
        raise ValueError("frame size mismatch in encode_video")

    container = av.open(out_path, "w")
    try:
        stream = container.add_stream("h264", rate=_as_rational(fps))
        stream.width, stream.height = width, height
        stream.pix_fmt = "yuv420p"
        stream.codec_context.gop_size = int(gop)
        stream.codec_context.max_b_frames = MAX_B_FRAMES
        stream.options = {
            "crf": str(int(crf)),
            "preset": X264_PRESET,
            "sc_threshold": "0",
        }
        for frame in frames:
            vf = av.VideoFrame.from_ndarray(np.ascontiguousarray(frame), format="rgb24")
            for pkt in stream.encode(vf):
                container.mux(pkt)
        for pkt in stream.encode():
            container.mux(pkt)
    finally:
        container.close()


def encode_video_to_bytes(
    frames: Sequence[np.ndarray],
    crf: int,
    gop: int,
    fps: float = 25.0,
) -> bytes:
    """Like :func:`encode_video` but returns the encoded bytes."""
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=True) as tmp:
        encode_video(frames, tmp.name, crf=crf, gop=gop, fps=fps)
        with open(tmp.name, "rb") as fh:
            return fh.read()


def read_media_bytes(path: str) -> bytes:
    """Read a file back as bytes (used to return the stego video to callers)."""
    with open(path, "rb") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Quality helpers
# ---------------------------------------------------------------------------

def frame_psnr(cover: np.ndarray, stego: np.ndarray) -> float:
    """PSNR between two rgb24 frames (inf when identical)."""
    cover = cover.astype(np.float64)
    stego = stego.astype(np.float64)
    mse = float(np.mean((cover - stego) ** 2))
    if mse == 0:
        return float("inf")
    return 20.0 * np.log10(255.0 / np.sqrt(mse))


def video_psnr(cover_path: str, stego_path: str, max_frames: int = 0) -> float:
    """Frame-averaged PSNR between two videos.

    When ``max_frames`` > 0, only that many leading frames are compared.
    Returns float("inf") for identical clips.
    """
    av = _require_av()
    try:
        cov = av.open(cover_path)
        stg = av.open(stego_path)
    except Exception as exc:  # pragma: no cover
        raise ValueError(f"Could not open videos for PSNR: {exc}") from exc
    try:
        cov_it = cov.decode(video=0)
        stg_it = stg.decode(video=0)
        total = 0.0
        count = 0
        for c, s in zip(cov_it, stg_it):
            total += frame_psnr(
                c.to_ndarray(format="rgb24"), s.to_ndarray(format="rgb24")
            )
            count += 1
            if max_frames and count >= max_frames:
                break
        return total / count if count else float("inf")
    finally:
        cov.close()
        stg.close()
