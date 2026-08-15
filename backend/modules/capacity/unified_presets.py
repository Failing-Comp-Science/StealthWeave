"""
Unified user-facing carrier presets (Harpocrates, 2026-08-09).

ONE user-facing preset axis replaces the previous two controls (carrier preset
+ payload compression). The frontend sends only a single ``preset`` id; this
module is the single source of truth that resolves it into a COMPLETE,
IMMUTABLE engine configuration via :func:`resolve_preset`. The backend may
still keep compression/carrier parameters as separate fields internally, but
every preset resolves to one complete configuration.

Presets
-------
* LOSSLESS              — "Lossless". Pendrive/disk/LAN/archive/direct file copy.
  No expected downstream lossy re-encode. Prefer lossless carrier formats and
  capacity-oriented embedding. DEFLATE is applied only when it actually shrinks
  the HSTG container (the production rule in ``modules.container.build_container``);
  capacity is modeled conservatively at ratio 1.0 and the EXACT serialized
  container size is measured at encode time. Ordinary H.264 is NOT called
  mathematically lossless.
* CHAT_STANDARD       — "Chat Standard". Ordinary social/chat upload; conservative
  capacity; more robust carrier parameters; expects downstream recompression.
* CHAT_HD             — "Chat HD". Higher-quality social/chat upload; intermediate
  capacity/robustness; still no survival guarantee after re-encoding.

Compatibility
-------------
Legacy engine presets light/standard/heavy (QF 95/85/75, CRF 18/23/28), the
Stage-2 carrier presets (chat_standard / chat_hd / lossless_high_capacity), and
the pre-rename LOSSLESS id (LOCAL_HIGH_CAPACITY) map onto these three unified
ids WITHOUT changing engine behavior:

    light  (QF>=90 / CRF<=20)  -> LOSSLESS
    standard (QF 80-89 / CRF 21-25) -> CHAT_HD
    heavy  (QF<80 / CRF>25)    -> CHAT_STANDARD
    lossless_high_capacity     -> LOSSLESS (alias)
    LOCAL_HIGH_CAPACITY        -> LOSSLESS (alias)
    chat_standard              -> CHAT_STANDARD
    chat_hd                    -> CHAT_HD

Precedence (locked by tests): 1. explicit unified ``preset`` id; 2. legacy
mapping (carrier_preset / light-standard-heavy / compress / compression_preset);
3. server default LOSSLESS.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from .presets import IMAGE_PRESETS, VIDEO_PRESETS

#: Capacity model version for the unified registry (bump when the model changes).
CAPACITY_MODEL_VERSION = "2.0"


class UnifiedPresetId(str, Enum):
    """Stable ids the frontend sends as the single ``preset`` field."""
    LOSSLESS = "LOSSLESS"
    CHAT_STANDARD = "CHAT_STANDARD"
    CHAT_HD = "CHAT_HD"


#: Legacy tokens that are accepted but always resolve through the legacy table.
_LEGACY_ALIASES = {
    # Pre-rename canonical id for the lossless preset.
    "LOCAL_HIGH_CAPACITY": UnifiedPresetId.LOSSLESS,
    # Stage-2 carrier presets
    "CHAT_STANDARD": UnifiedPresetId.CHAT_STANDARD,
    "CHAT_HD": UnifiedPresetId.CHAT_HD,
    "LOSSLESS_HIGH_CAPACITY": UnifiedPresetId.LOSSLESS,
    # legacy channel presets
    "NO_COMPRESSION": UnifiedPresetId.LOSSLESS,
}


def _tier_for_image_qf(qf: int) -> UnifiedPresetId:
    """Map a legacy JPEG quality factor onto the unified preset (matches the
    API's ``_carrier_preset_for_qf`` boundaries: light>=90, standard>=80)."""
    if qf >= 90:
        return UnifiedPresetId.LOSSLESS
    if qf >= 80:
        return UnifiedPresetId.CHAT_HD
    return UnifiedPresetId.CHAT_STANDARD


def _tier_for_video_crf(crf: int) -> UnifiedPresetId:
    """Map a legacy CRF onto the unified preset (light<=20, standard<=25)."""
    if crf <= 20:
        return UnifiedPresetId.LOSSLESS
    if crf <= 25:
        return UnifiedPresetId.CHAT_HD
    return UnifiedPresetId.CHAT_STANDARD


#: Engine tier id the capacity calculators report for each unified preset.
UNIFIED_TO_ENGINE_TIER: Dict[UnifiedPresetId, str] = {
    UnifiedPresetId.LOSSLESS: "light",
    UnifiedPresetId.CHAT_HD: "standard",
    UnifiedPresetId.CHAT_STANDARD: "heavy",
}

#: Legacy engine tier -> unified preset (reverse of the above).
ENGINE_TIER_TO_UNIFIED = {tier: pid for pid, tier in UNIFIED_TO_ENGINE_TIER.items()}

#: TEXT_FILE compression factor per unified preset: LOSSLESS is conservatively 1.0
#: (measured from the ACTUAL serialized container at encode time), CHAT_* use
#: the empirically measured median DEFLATE ratio (COMPRESSION_PRESETS.md).
UNIFIED_TEXT_COMPRESSION_FACTOR: Dict[UnifiedPresetId, float] = {
    UnifiedPresetId.LOSSLESS: 1.0,
    UnifiedPresetId.CHAT_STANDARD: 1.35,
    UnifiedPresetId.CHAT_HD: 1.35,
}

#: QIM strength (delta) per unified preset for the JPEG DCT engine.
UNIFIED_QIM_DELTA: Dict[UnifiedPresetId, float] = {
    UnifiedPresetId.LOSSLESS: 2.0,  # finer levels, more margin at QF 95
    UnifiedPresetId.CHAT_STANDARD: 1.0,
    UnifiedPresetId.CHAT_HD: 1.0,
}


@dataclass(frozen=True)
class UnifiedPreset:
    """A complete user-facing preset definition (single source of truth)."""
    id: UnifiedPresetId
    label: str
    description: str
    supported_modalities: List[str]           # ["image", "video"]
    payload_compression_policy: str           # "deflate_if_smaller"
    image_algorithm: str                      # "spatial_lsb" | "block_dct_qim"
    video_algorithm: str                      # "iframe_dct_qim"
    bits_per_channel: int                     # LSB planes (spatial engine)
    bits_per_coefficient: int                 # DCT/QIM bits per usable slot
    frame_keyframe_policy: str                # "iframe_grid"
    qim_strength: float                       # QIM delta (JPEG engine)
    jpeg_quality: int                         # DCT-QIM target QF (JPEG covers)
    video_crf: int                            # H.264 target CRF (video covers)
    image_derate: float                       # reliability derate at jpeg_quality
    output_format_policy: str                 # "preserve_cover_format"
    expected_downstream_transformation: str
    safety_margin_ratio: float
    capacity_model_version: str
    direct_extraction_expected: bool
    warnings: List[str] = field(default_factory=list)


UNIFIED_PRESETS: Dict[UnifiedPresetId, UnifiedPreset] = {
    UnifiedPresetId.LOSSLESS: UnifiedPreset(
        id=UnifiedPresetId.LOSSLESS,
        label="Lossless",
        description=(
            "For pendrives, disks, LAN, archives and direct file copies — "
            "maximum capacity with byte-exact extraction. PNG/BMP covers use "
            "lossless spatial LSB embedding in the browser; JPEG covers use the "
            "highest DCT quality (QF 95); video uses CRF 18 (near-lossless). "
            "No social-media robustness margins. The file extracts perfectly "
            "when copied without re-encoding."
        ),
        supported_modalities=("image", "video"),
        payload_compression_policy="deflate_if_smaller",
        image_algorithm="spatial_lsb",
        video_algorithm="iframe_dct_qim",
        bits_per_channel=1,
        bits_per_coefficient=1,
        frame_keyframe_policy="iframe_grid",
        qim_strength=2.0,
        jpeg_quality=95,
        video_crf=18,
        image_derate=1.0,
        output_format_policy="preserve_cover_format",
        expected_downstream_transformation="none (byte-exact file copy)",
        safety_margin_ratio=0.0,
        capacity_model_version=CAPACITY_MODEL_VERSION,
        direct_extraction_expected=True,
        warnings=(
            "Byte-exact extraction is expected when the file is copied without re-encoding.",
            "NOT guaranteed to survive: WhatsApp, Messenger, social media, any lossy re-encode.",
            "H.264 output is near-lossless (CRF 18), not mathematically lossless.",
        ),
    ),
    UnifiedPresetId.CHAT_STANDARD: UnifiedPreset(
        id=UnifiedPresetId.CHAT_STANDARD,
        label="Chat Standard",
        description=(
            "For ordinary social/chat uploads. Conservative payload capacity "
            "with more robust carrier parameters (JPEG QF 75 / CRF 28); "
            "downstream resizing and recompression are expected."
        ),
        supported_modalities=("image", "video"),
        payload_compression_policy="deflate_if_smaller",
        image_algorithm="block_dct_qim",
        video_algorithm="iframe_dct_qim",
        bits_per_channel=1,
        bits_per_coefficient=1,
        frame_keyframe_policy="iframe_grid",
        qim_strength=1.0,
        jpeg_quality=75,
        video_crf=28,
        image_derate=0.4,
        output_format_policy="preserve_cover_format",
        expected_downstream_transformation="social/chat recompression (~Q60-70)",
        safety_margin_ratio=0.10,
        capacity_model_version=CAPACITY_MODEL_VERSION,
        direct_extraction_expected=False,
        warnings=(
            "Designed for ordinary chat uploads — recompression is expected.",
            "Survival after re-encoding is NOT guaranteed.",
            "Conservative capacity by design.",
        ),
    ),
    UnifiedPresetId.CHAT_HD: UnifiedPreset(
        id=UnifiedPresetId.CHAT_HD,
        label="Chat HD",
        description=(
            "For higher-quality social/chat uploads. Intermediate capacity and "
            "robustness (JPEG QF 85 / CRF 23). Still does not guarantee "
            "survival after re-encoding."
        ),
        supported_modalities=("image", "video"),
        payload_compression_policy="deflate_if_smaller",
        image_algorithm="block_dct_qim",
        video_algorithm="iframe_dct_qim",
        bits_per_channel=1,
        bits_per_coefficient=1,
        frame_keyframe_policy="iframe_grid",
        qim_strength=1.0,
        jpeg_quality=85,
        video_crf=23,
        image_derate=0.6,
        output_format_policy="preserve_cover_format",
        expected_downstream_transformation="higher-quality chat upload (~Q80-85)",
        safety_margin_ratio=0.07,
        capacity_model_version=CAPACITY_MODEL_VERSION,
        direct_extraction_expected=False,
        warnings=(
            "Designed for HD chat uploads — moderate recompression expected.",
            "Survival after re-encoding is NOT guaranteed.",
        ),
    ),
}

#: Public order for the UI / API (default first).
PRESET_ORDER: List[UnifiedPresetId] = [
    UnifiedPresetId.LOSSLESS,
    UnifiedPresetId.CHAT_STANDARD,
    UnifiedPresetId.CHAT_HD,
]

DEFAULT_PRESET = UnifiedPresetId.LOSSLESS


@dataclass(frozen=True)
class ResolvedPresetConfig:
    """The complete, immutable effective configuration for one encode/capacity.

    Produced by :func:`resolve_preset`; consumed identically by the capacity
    calculators, the encode pipelines, the API metadata and the benchmark
    harness. ``compression_requested`` may be overridden by LEGACY clients that
    still send ``compress`` (documented compatibility), but for explicit
    unified presets it always comes from the preset's policy.
    """
    preset_id: UnifiedPresetId
    label: str
    modality: str                          # "image" | "video"
    carrier_format: str                    # e.g. "png" | "jpeg" | "mp4"
    payload_type: str                      # e.g. "TEXT_MESSAGE" | "IMAGE"
    engine: str                            # "spatial_lsb" | "jpeg_dct_qim" | "video_iframe_dct_qim"
    payload_compression_policy: str        # "deflate_if_smaller"
    compression_requested: bool
    bits_per_channel: int
    bits_per_coefficient: int
    frame_keyframe_policy: str
    qim_strength: float
    jpeg_quality: int
    video_crf: int
    image_derate: float
    text_compression_factor: float
    output_format_policy: str
    expected_downstream_transformation: str
    safety_margin_ratio: float
    capacity_model_version: str
    direct_extraction_expected: bool
    warnings: tuple
    engine_tier: str                       # capacity-model tier ("light"|"standard"|"heavy")

    @property
    def container_preset_id(self) -> str:
        """Legacy CompressionPresetId token for the container header."""
        return self.engine_tier


def get_unified_preset(preset_id: str | UnifiedPresetId) -> UnifiedPreset:
    """Resolve a unified preset by id (case-insensitive) or enum."""
    if isinstance(preset_id, UnifiedPresetId):
        return UNIFIED_PRESETS[preset_id]
    token = (preset_id or "").strip().upper()
    if token in _LEGACY_ALIASES:
        preset_id = _LEGACY_ALIASES[token]
    elif token in UnifiedPresetId.__members__:
        preset_id = UnifiedPresetId(token)
    else:
        raise ValueError(f"Unknown unified preset: {preset_id}")
    return UNIFIED_PRESETS[preset_id]


def list_unified_presets() -> List[UnifiedPreset]:
    """All unified presets in display order."""
    return [UNIFIED_PRESETS[pid] for pid in PRESET_ORDER]


def is_unified_preset_token(token: str) -> bool:
    """True when ``token`` is an explicit unified preset id (not a legacy one)."""
    t = (token or "").strip().upper()
    return t in UnifiedPresetId.__members__


def resolve_preset(
    preset_id: str | UnifiedPresetId,
    modality: str,
    carrier_format: str,
    payload_type: str,
    *,
    compression_requested: Optional[bool] = None,
) -> ResolvedPresetConfig:
    """Resolve a preset into the complete effective engine configuration.

    Args:
        preset_id: unified preset id (``LOSSLESS`` | ``CHAT_STANDARD`` |
            ``CHAT_HD``) or a legacy alias (``light``/``standard``/``heavy``,
            ``chat_standard``/``chat_hd``/``lossless_high_capacity``).
        modality: "image" | "video".
        carrier_format: the cover's concrete format, e.g. "png", "jpeg", "mp4"
            (drives the engine selection).
        payload_type: container payload type ("TEXT_MESSAGE" | "TEXT_FILE" |
            "IMAGE").
        compression_requested: legacy override (from ``compress`` /
            ``payload_compression``); when given it wins over the preset policy
            so old clients keep legacy semantics. When None the preset's policy
            governs.

    Raises:
        ValueError: unknown preset id or unsupported modality/format/payload
            combination.
    """
    preset = get_unified_preset(preset_id)
    mod = (modality or "").strip().lower()
    fmt = (carrier_format or "").strip().lower()
    ptype = (payload_type or "").strip().upper()

    if mod not in ("image", "video"):
        raise ValueError(f"Unsupported modality '{modality}'. Expected image | video.")
    if mod == "video" and fmt not in {"mp4", "webm", "mov", "mkv", "m4v", "ogv", "avi"}:
        raise ValueError(f"Unsupported video format '{carrier_format}'.")
    if mod == "image" and fmt not in {"png", "bmp", "jpeg", "jpg", "webp", "gif"}:
        raise ValueError(f"Unsupported image format '{carrier_format}'.")
    if ptype not in {"TEXT_MESSAGE", "TEXT_FILE", "IMAGE"}:
        raise ValueError(f"Unsupported payload type '{payload_type}'.")

    # Engine selection is FORMAT-driven for video; all still-image formats
    # use spatial LSB (JPEG is decoded to pixels and saved as PNG).
    if mod == "video":
        engine = "video_iframe_dct_qim"
    else:
        engine = "spatial_lsb"

    if compression_requested is None:
        compression_requested = True  # every preset policy is "deflate_if_smaller"

    tier = UNIFIED_TO_ENGINE_TIER[preset.id]
    return ResolvedPresetConfig(
        preset_id=preset.id,
        label=preset.label,
        modality=mod,
        carrier_format=fmt,
        payload_type=ptype,
        engine=engine,
        payload_compression_policy=preset.payload_compression_policy,
        compression_requested=compression_requested,
        bits_per_channel=preset.bits_per_channel,
        bits_per_coefficient=preset.bits_per_coefficient,
        frame_keyframe_policy=preset.frame_keyframe_policy,
        qim_strength=preset.qim_strength,
        jpeg_quality=preset.jpeg_quality,
        video_crf=preset.video_crf,
        image_derate=preset.image_derate,
        text_compression_factor=UNIFIED_TEXT_COMPRESSION_FACTOR[preset.id],
        output_format_policy=preset.output_format_policy,
        expected_downstream_transformation=preset.expected_downstream_transformation,
        safety_margin_ratio=preset.safety_margin_ratio,
        capacity_model_version=preset.capacity_model_version,
        direct_extraction_expected=preset.direct_extraction_expected,
        warnings=tuple(preset.warnings),
        engine_tier=tier,
    )


def legacy_engine_tier_to_unified(tier: str) -> UnifiedPresetId:
    """Map a legacy engine tier id (light/standard/heavy) to a unified preset."""
    try:
        return ENGINE_TIER_TO_UNIFIED[tier]
    except KeyError:
        raise ValueError(f"Unknown legacy engine tier '{tier}'")


def legacy_qf_to_unified(qf: int) -> UnifiedPresetId:
    """Map a legacy JPEG quality factor to a unified preset."""
    return _tier_for_image_qf(int(qf))


def legacy_crf_to_unified(crf: int) -> UnifiedPresetId:
    """Map a legacy video CRF to a unified preset."""
    return _tier_for_video_crf(int(crf))


#: Engine preset lookup by unified id (for the embed pipelines).
_QF_BY_UNIFIED = {pid: preset.jpeg_quality for pid, preset in UNIFIED_PRESETS.items()}
_CRF_BY_UNIFIED = {pid: preset.video_crf for pid, preset in UNIFIED_PRESETS.items()}
_DELTA_BY_UNIFIED = {pid: preset.qim_strength for pid, preset in UNIFIED_PRESETS.items()}


def unified_to_jpeg_qf(preset_id: UnifiedPresetId) -> int:
    return _QF_BY_UNIFIED[preset_id]


def unified_to_video_crf(preset_id: UnifiedPresetId) -> int:
    return _CRF_BY_UNIFIED[preset_id]


def unified_to_qim_delta(preset_id: UnifiedPresetId) -> float:
    return _DELTA_BY_UNIFIED[preset_id]


def unified_to_engine_preset_id(preset_id: UnifiedPresetId) -> str:
    """Legacy ``CompressionPresetId`` token for the container header byte."""
    return UNIFIED_TO_ENGINE_TIER[preset_id]


def unified_to_container_preset(preset_id: UnifiedPresetId):
    """Map a unified preset to the channel-level :class:`CompressionPreset`
    consumed by the capacity calculators (TEXT_FILE compression multiplier).

    LOSSLESS models capacity conservatively at ratio 1.0 (the exact
    serialized container size is measured at encode time); the CHAT_* presets
    use the empirically measured DEFLATE ratio via the matching container
    preset.
    """
    from ..container import CompressionPreset

    return {
        UnifiedPresetId.LOSSLESS: CompressionPreset.NO_COMPRESSION,
        UnifiedPresetId.CHAT_STANDARD: CompressionPreset.CHAT_STANDARD,
        UnifiedPresetId.CHAT_HD: CompressionPreset.CHAT_HD,
    }[preset_id]


# Keep the tier presets reachable for the capacity calculators' engine tiers.
ENGINE_IMAGE_PRESETS = IMAGE_PRESETS
ENGINE_VIDEO_PRESETS = VIDEO_PRESETS
