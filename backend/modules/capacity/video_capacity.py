"""
Preset-aware video capacity calculator (task step 4).

``video_capacity`` returns, for EVERY video preset in one call, the usable
payload capacity computed from I-frame DCT coefficient slots at that preset's
target CRF. Model + citations: see ``presets.py``.

Capacity is reported as:
  * ``max_bytes_per_minute_text_message`` / ``..._text_file`` - the *marginal*
    coded rate each minute of I-frames contributes (the fixed container
    overhead is a one-time cost, not part of a per-minute rate).
  * ``max_bytes_image`` - a concrete whole-clip figure for a single embedded
    image payload, with the fixed container overhead subtracted once.

I-frame model: H.264 places an intra (I) frame at least every GOP; typical
streaming encodes force a keyframe every ~2 s [x264 keyint]. We assume one
usable intra frame every ``GOP_SECONDS`` and estimate usable coefficients per
intra frame by sampling decoded frames and running the JPEG 8x8 texture
estimator at the preset's ``qf_equiv`` (see presets.py for the CRF<->QF bridge
rationale [H264][x264]).
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

import numpy as np

try:
    import cv2  # opencv-python is pinned in requirements.txt
except Exception:  # pragma: no cover - environment guard
    cv2 = None

from ..container import (
    CompressionPreset,
    container_overhead_bytes,
    ecc_expansion_ratio,
)
from ._dct import analyze_texture, rgb_to_luma
from .presets import (
    BITS_PER_COEFF,
    IMAGE_COMPRESSION_RATIO,
    SHRINKAGE_RETENTION,
    VIDEO_PRESETS,
    scaled_luma_table,
)

#: Assumed spacing between usable intra (I) frames, in seconds [x264 keyint].
GOP_SECONDS = 2.0
#: Max decoded frames sampled to estimate per-frame texture (bounds runtime).
_MAX_SAMPLES = 8
_FILENAME_BUDGET = 64
_MIME_BUDGET = 32


class VideoProbeError(ValueError):
    """Raised when a video cannot be opened / probed."""


def _probe_and_sample(path: str):
    """Open ``path``; return (width, height, fps, duration_sec, [luma frames])."""
    if cv2 is None:
        raise VideoProbeError("OpenCV (cv2) is unavailable; cannot probe video")
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

        return width, height, fps, duration, sample_luma
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
    width, height, fps, duration, samples = _probe_and_sample(path)
    if duration_sec_hint and duration_sec_hint > 0:
        duration = float(duration_sec_hint)

    iframes_total = max(1, int(math.floor(duration / GOP_SECONDS))) if duration > 0 else 1
    iframes_per_min = 60.0 / GOP_SECONDS

    overhead_message = container_overhead_bytes(use_ecc=True, encrypted=True)
    overhead_image = container_overhead_bytes(
        original_filename="x" * _FILENAME_BUDGET,
        mime_type="x" * _MIME_BUDGET,
        use_ecc=True,
        encrypted=True,
    )
    ecc = ecc_expansion_ratio()
    # NOTE (calibrated 2026-08-08): ``text_compression_factor`` is now the
    # empirically measured TEXT_FILE DEFLATE ratio (median 1.35 on the
    # deterministic synthetic corpus; see docs/COMPRESSION_PRESETS.md). The
    # rate model multiplies ``coded_per_min`` directly, so the float factor
    # is used as-is (no integer truncation).
    text_factor = compression_preset.text_compression_factor

    results: List[Dict] = []
    for preset in VIDEO_PRESETS:
        quant = scaled_luma_table(preset.qf_equiv)

        # Average usable AC slots per (intra) frame across the samples.
        per_frame_slots = []
        total_blocks = high_blocks = 0
        for luma in samples:
            tb, hb, slots = analyze_texture(luma, quant)
            per_frame_slots.append(slots)
            total_blocks, high_blocks = tb, hb  # representative (uniform size)
        usable_slots_per_iframe = float(np.mean(per_frame_slots)) * SHRINKAGE_RETENTION

        # Marginal per-minute embeddable bytes (overhead excluded from a rate).
        embeddable_per_min = usable_slots_per_iframe * iframes_per_min * BITS_PER_COEFF / 8.0
        coded_per_min = embeddable_per_min / ecc
        max_pm_message = int(math.floor(coded_per_min))
        max_pm_file = int(math.floor(coded_per_min * text_factor))

        # Whole-clip capacity for a single image payload (overhead once).
        embeddable_total = usable_slots_per_iframe * iframes_total * BITS_PER_COEFF / 8.0
        coded_total = max(0.0, (embeddable_total - overhead_image)) / ecc
        max_image = int(math.floor(coded_total * IMAGE_COMPRESSION_RATIO))

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
            "iframes_per_minute": iframes_per_min,
            "usable_coeff_slots_per_iframe": int(usable_slots_per_iframe),
            "width": width,
            "height": height,
            "fps": round(fps, 3),
            "duration_sec": round(duration, 3),
        })
    return results
