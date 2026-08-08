"""
Preset-aware capacity calculator (audit-derived; see ``presets.py`` for the
model and citations). Public entry points:

    image_capacity(rgb, compression_preset=None)        -> capacity for every image preset
    video_capacity(path, duration_hint, compression_preset=None) -> capacity for every video preset

Channel-level compression is expressed with the first-class
``CompressionPreset`` (NO_COMPRESSION / CHAT_STANDARD / CHAT_HD) defined in
``modules.container`` — see its docstring and ``docs/COMPRESSION_PRESETS.md``.
"""
from ..container import CompressionPreset
from .image_capacity import image_capacity
from .video_capacity import video_capacity, VideoProbeError
from .presets import IMAGE_PRESETS, VIDEO_PRESETS

__all__ = [
    "image_capacity",
    "video_capacity",
    "VideoProbeError",
    "IMAGE_PRESETS",
    "VIDEO_PRESETS",
    "CompressionPreset",
]
