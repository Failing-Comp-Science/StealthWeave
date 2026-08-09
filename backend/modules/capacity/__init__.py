"""
Preset-aware capacity calculator (audit-derived; see ``presets.py`` for the
model and citations). Public entry points:

    image_capacity(rgb, compression_preset=None)        -> capacity for every image preset
    video_capacity(path, duration_hint, compression_preset=None) -> capacity for every video preset

Channel-level compression is expressed with the first-class
``CompressionPreset`` (NO_COMPRESSION / CHAT_STANDARD / CHAT_HD) defined in
``modules.container`` — see its docstring and ``COMPRESSION_PRESETS.md``.

Carrier presets (new in Stage 2):
    CarrierPreset, CarrierPresetId, CarrierModality,
    CARRIER_PRESETS, get_carrier_preset, list_carrier_presets,
    carrier_preset_to_image_qf, carrier_preset_to_video_crf,
    carrier_preset_to_payload_compression_default, carrier_preset_to_lsb_bpc

Accounting breakdown:
    AccountingBreakdown, compute_accounting_breakdown  -> itemized overhead
"""
from ..container import CompressionPreset
from .accounting import (
    AccountingBreakdown,
    compute_accounting_breakdown,
)
from .carrier_presets import (
    CarrierPreset,
    CarrierPresetId,
    CarrierModality,
    CARRIER_PRESETS,
    get_carrier_preset,
    list_carrier_presets,
    carrier_preset_to_image_qf,
    carrier_preset_to_video_crf,
    carrier_preset_to_payload_compression_default,
    carrier_preset_to_lsb_bpc,
)
from .image_capacity import (
    LOSSLESS_PRESET_ID,
    image_capacity,
    spatial_capacity,
)
from .video_capacity import video_capacity, VideoProbeError
from .presets import IMAGE_PRESETS, VIDEO_PRESETS

__all__ = [
    "image_capacity",
    "spatial_capacity",
    "LOSSLESS_PRESET_ID",
    "video_capacity",
    "VideoProbeError",
    "IMAGE_PRESETS",
    "VIDEO_PRESETS",
    "CompressionPreset",
    "AccountingBreakdown",
    "compute_accounting_breakdown",
    "CarrierPreset",
    "CarrierPresetId",
    "CarrierModality",
    "CARRIER_PRESETS",
    "get_carrier_preset",
    "list_carrier_presets",
    "carrier_preset_to_image_qf",
    "carrier_preset_to_video_crf",
    "carrier_preset_to_payload_compression_default",
    "carrier_preset_to_lsb_bpc",
]
