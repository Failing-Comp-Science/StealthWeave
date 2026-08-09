"""
Preset-aware video capacity calculator (I-frame keyframe-grid model).

``video_capacity`` returns, for EVERY video preset in one call, the usable
payload capacity computed from the I-frame DCT mid-band carrier slots on the
cover's ACTUAL I-frame keyframe grid. Model + citations: see ``presets.py``;
this module's carrier rule is ``_dct.count_mid_usable_blocks``.

The previous model bridged CRF to a JPEG quality-equivalent and ran the JPEG
8x8 texture estimator, which reported roughly *zero* carriers at
standard/heavy CRFs. That was a modeling artifact: the real embedder
(``modules.video_stego``) never quantizes to a JPEG table -- it raw-DCT's the
I-frame luma, treats every block with >= ``MIN_AC_MID`` mid-band coefficients
as a usable carrier (CRF-independent), and snaps parity levels under a closed
loop against the H.264 re-encode. The evaluation benchmark confirmed the
engine's embed ceiling is CRF-independent (identical max payload at CRF 18 /
23 / 28), so the per-preset difference is robustness (``expected_ber``) and
the QIM delta, not the carrier count. This module now models the engine's
actual eligibility rule.

The I-frame grid is derived from the cover's own keyframes (PyAV
``probe_video``/``keyframe_grid``), matching the embedder's ``_grid_indices``:
the payload rides I-frames at display indices 0, G, 2G, ... where G is the
cover's median keyframe spacing. Texture is measured ONLY on those I-frames
(never on interpolated P/B frames), so the estimated slots per I-frame
reflect the carriers the engine actually uses.

Capacity is reported as:
  * ``max_bytes_per_minute_text_message`` / ``..._text_file`` - the *marginal*
    coded rate each minute of I-frame contributes (the fixed container
    overhead is a one-time cost, not part of a per-minute rate).
  * ``max_bytes_image`` - a concrete whole-clip figure for a single embedded
    image payload, with the fixed container overhead subtracted once.

Carrier model (mirrors the engine; validated by the evaluation harness): the
embedder does *not* quantize to a JPEG table -- it snaps raw mid-band DCT
magnitudes to a QIM parity level and closes the loop against the H.264
re-encode. Usable blocks per I-frame therefore follow the engine's eligibility
rule (>= ``MIN_AC_MID`` raw mid-band coefficients above ``TINY``) and are
CRF-independent; the per-preset differences are robustness (``expected_ber``)
and the QIM delta, not the carrier count [H264][x264]. Each usable block
carries one channel bit (BITS_PER_BLOCK == 1, REPETITIONS == 1), so the
per-I-frame slot count maps 1:1 onto channel-coded capacity (fitted exactly
via ``modules.capacity.accounting``, including the outer channel RS layer).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    from ..video_stego._codec import keyframe_grid, probe_video
except Exception:  # pragma: no cover - PyAV is optional for the calculator
    keyframe_grid = probe_video = None


def _require_cv2():
    """Import OpenCV lazily, ONLY when PyAV is unavailable.

    IMPORTANT (collision mitigation, WORK_AND_FAILURES §4.2.4): OpenCV and PyAV
    both bundle their own FFmpeg builds (libavdevice 61.x etc.). Importing cv2
    BEFORE PyAV makes the interpreter load two different ``libavdevice``
    dylibs and emits ``Class AVFFrameReceiver is implemented in both ...``
    objc warnings that can cause spurious casting failures / mysterious crashes
    in the audio/video pipeline. The video path uses PyAV exclusively
    (``modules.video_stego``); OpenCV here is only a fallback prober for
    environments without PyAV, so it is imported lazily inside the fallback
    function and NEVER loaded in the normal PyAV-present path.
    """
    import cv2  # opencv-python is pinned in requirements.txt

    return cv2

from ..container import (
    CompressionPreset,
    container_overhead_bytes,
)
from ._dct import count_mid_usable_blocks, rgb_to_luma
from .accounting import max_payload_channel_bits
from .presets import (
    IMAGE_COMPRESSION_RATIO,
    VIDEO_PRESETS,
)

#: Max decoded I-frames sampled to estimate per-frame texture (bounds runtime).
_MAX_SAMPLES = 8
_FILENAME_BUDGET = 64
_MIME_BUDGET = 32


class VideoProbeError(ValueError):
    """Raised when a video cannot be opened / probed."""


def _probe_and_sample(
    path: str,
) -> Tuple[int, int, float, float, int, float, List[np.ndarray]]:
    """Probe ``path``; return (w, h, fps, duration_sec, iframes_total,
    iframes_per_min, [luma I-frames]).

    Uses PyAV to read the cover's real I-frame keyframe grid (exactly what the
    embedder uses); falls back to OpenCV uniform sampling with a 2 s GOP
    estimate when PyAV is unavailable.
    """
    if probe_video is not None:
        return _probe_pyav(path)
    return _probe_cv2(path)


def _probe_pyav(
    path: str,
) -> Tuple[int, int, float, float, int, float, List[np.ndarray]]:
    from ..video_stego._codec import decode_rgb

    width, height, fps, nb_frames, _keyframes = probe_video(path)
    if fps <= 0:
        fps = 25.0
    if width <= 0 or height <= 0:
        raise VideoProbeError("Video has invalid dimensions")
    duration = (nb_frames / fps) if nb_frames > 0 else 0.0

    gop = keyframe_grid(path, fps)
    grid = list(range(0, nb_frames, max(1, int(gop))))  # engine's _grid_indices
    iframes_total = len(grid) if grid else 1
    iframes_per_min = 60.0 * fps / max(1, int(gop))

    # Sample I-frame luma ONLY (the carriers the engine embeds into).
    wanted = set(grid[:: max(1, len(grid) // _MAX_SAMPLES)][:_MAX_SAMPLES])
    sample_luma: List[np.ndarray] = []
    for idx, rgb, _is_keyframe in decode_rgb(path):
        if idx in wanted:
            sample_luma.append(rgb_to_luma(rgb))
    if not sample_luma:
        raise VideoProbeError("Could not decode any I-frames from video")
    return width, height, fps, duration, iframes_total, iframes_per_min, sample_luma


def _probe_cv2(
    path: str,
) -> Tuple[int, int, float, float, int, float, List[np.ndarray]]:
    """OpenCV fallback: uniform frame samples, 2 s GOP estimate."""
    try:
        cv2 = _require_cv2()
    except Exception as exc:  # noqa: BLE001
        raise VideoProbeError(
            "Neither PyAV nor OpenCV is available; cannot probe video"
        ) from exc
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise VideoProbeError("Could not open video file")
    try:
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(cap.get(cv2.CAP_PROP_FPS)) or 0.0
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if fps <= 0:
            fps = 25.0  # sane default when the container omits it
        duration = (frame_count / fps) if frame_count > 0 else 0.0

        if width <= 0 or height <= 0:
            raise VideoProbeError("Video has invalid dimensions")

        # Sample frames uniformly across the clip.
        sample_luma = []
        n_samples = _MAX_SAMPLES if frame_count > 0 else 1
        indices = (
            np.linspace(0, max(frame_count - 1, 0), num=min(n_samples, max(frame_count, 1)), dtype=int)
            if frame_count > 0 else [0]
        )
        for idx in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            # OpenCV returns BGR; luma weights are channel-order aware.
            luma = rgb_to_luma(frame[:, :, ::-1])
            sample_luma.append(luma)

        if not sample_luma:
            # Fall back to reading the very first frame sequentially.
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = cap.read()
            if ok and frame is not None:
                sample_luma.append(rgb_to_luma(frame[:, :, ::-1]))

        if not sample_luma:
            raise VideoProbeError("Could not decode any frames from video")

        gop = 2.0 * fps
        iframes_total = max(1, int(np.ceil(duration / 2.0))) if duration > 0 else 1
        iframes_per_min = 60.0 / 2.0
        return width, height, fps, duration, iframes_total, iframes_per_min, sample_luma
    finally:
        cap.release()


def video_capacity(
    path: str,
    duration_sec_hint: Optional[float] = None,
    compression_preset: CompressionPreset = CompressionPreset.NO_COMPRESSION,
) -> List[Dict]:
    """Capacity for every video preset under a channel compression preset.

    Args:
        path: filesystem path to the cover video.
        duration_sec_hint: optional duration override when the container's
            metadata is missing/unreliable (e.g. from a browser probe).
        compression_preset: channel-level preset governing the TEXT_FILE
            compression multiplier (default NO_COMPRESSION => factor 1.0).
    """
    width, height, fps, duration, iframes_total, iframes_per_min, samples = _probe_and_sample(path)
    if duration_sec_hint and duration_sec_hint > 0:
        duration = float(duration_sec_hint)
        # The I-frame grid scales with the (hinted) clip length: keep the
        # per-minute I-frame rate and re-derive the whole-clip grid so a
        # longer cover exposes proportionally more carriers.
        iframes_total = max(1, int(round(duration / 60.0 * iframes_per_min)))

    overhead_image = container_overhead_bytes(
        original_filename="x" * _FILENAME_BUDGET,
        mime_type="x" * _MIME_BUDGET,
        use_ecc=True,
        encrypted=True,
    )
    # NOTE (calibrated 2026-08-08): ``text_compression_factor`` is now the
    # empirically measured TEXT_FILE DEFLATE ratio (median 1.35 on the
    # deterministic synthetic corpus; see docs/COMPRESSION_PRESETS.md). The
    # rate model multiplies ``coded_per_min`` directly, so the float factor
    # is used as-is (no integer truncation).
    text_factor = compression_preset.text_compression_factor

    results: List[Dict] = []
    for preset in VIDEO_PRESETS:
        # Usable carrier blocks per sampled I-frame, using the engine's OWN
        # eligibility rule (raw mid-band DCT, CRF-independent).
        per_frame_slots = [count_mid_usable_blocks(luma) for luma in samples]
        usable_slots_per_iframe = float(np.mean(per_frame_slots))

        # Marginal per-minute rate (fixed container overhead excluded from a
        # rate). Each usable block carries one channel bit; the exact channel
        # accounting (container RS + channel RS + FRAMING_BITS) sizes the
        # payload.
        min_slots_per_min = usable_slots_per_iframe * iframes_per_min
        max_pm_message = int(max_payload_channel_bits(int(min_slots_per_min), 0, ratio=1.0))
        max_pm_file = int(max_payload_channel_bits(int(min_slots_per_min), 0, ratio=text_factor))

        # Whole-clip image payload (overhead subtracted once).
        slots_total = usable_slots_per_iframe * iframes_total
        max_image = int(
            max_payload_channel_bits(
                int(slots_total), overhead_image, ratio=IMAGE_COMPRESSION_RATIO
            )
        )

        results.append({
            "id": preset.id,
            "name": preset.name,
            "description": preset.description,
            "technique": preset.technique,
            "target_crf": preset.target_crf,
            "expected_ber": preset.expected_ber,
            "survivability_description": preset.survivability_description,
            "compression_preset": compression_preset.value,
            "text_compression_factor": text_factor,
            "max_bytes_per_minute_text_message": max_pm_message,
            "max_bytes_per_minute_text_file": max_pm_file,
            "max_bytes_image": max_image,
            # diagnostics
            "iframes_total": iframes_total,
            "iframes_per_minute": round(iframes_per_min, 3),
            "usable_coeff_slots_per_iframe": int(usable_slots_per_iframe),
            "width": width,
            "height": height,
            "fps": round(fps, 3),
            "duration_sec": round(duration, 3),
        })
    return results
