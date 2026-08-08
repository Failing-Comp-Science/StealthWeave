"""
Video steganography module (H.264-robust compressed-domain DCT-QIM).

Public entry points:
    embed_video(cover_path, container, preset, password, out_path) -> (bytes, EmbedStats)
    extract_video(stego_path, password) -> container bytes
    VideoEmbedder                  (``modules.base.BaseEmbedder`` subclass)
"""
from .engine import (
    DELTA_BY_CRF,
    EmbedStats,
    VideoEmbedError,
    VideoEmbedder,
    embed_video,
    extract_video,
)

__all__ = [
    "embed_video",
    "extract_video",
    "VideoEmbedder",
    "VideoEmbedError",
    "EmbedStats",
    "DELTA_BY_CRF",
]
