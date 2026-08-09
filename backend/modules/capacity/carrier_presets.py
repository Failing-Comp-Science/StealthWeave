"""
Typed Carrier Preset Catalog (Harpocrates).

Defines the first-class carrier presets that govern how much data a cover can
carry and under what transfer conditions it survives. This is the *carrier*
axis (independent of the payload-compression axis).

Each preset carries enough metadata for the UI to show helper text, for the
capacity model to apply the correct derating/safety margins, and for the
encode pipeline to select the correct engine parameters.

The presets are:

  * CHAT_STANDARD   - WhatsApp/Messenger default upload (~1600px, QF 60-70)
  * CHAT_HD         - WhatsApp/Messenger HD toggle (~3000-4096px, QF 80-85)
  * LOSSLESS_HIGH_CAPACITY - Pendrive / local disk / LAN transfer (no re-encode)

NOTE: The legacy light/standard/heavy presets (QF 95/85/75) remain as
internal engine presets for the DCT-QIM image engine. They are mapped from
the carrier preset at encode time. The video engine maps carrier presets
to CRF values (18/23/28).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class CarrierModality(str, Enum):
    """Cover modality this carrier preset applies to."""
    IMAGE = "image"
    VIDEO = "video"
    BOTH = "both"


class CarrierPresetId(str, Enum):
    """Stable IDs for carrier presets (used in API requests)."""
    CHAT_STANDARD = "chat_standard"
    CHAT_HD = "chat_hd"
    LOSSLESS_HIGH_CAPACITY = "lossless_high_capacity"


@dataclass(frozen=True)
class CarrierPreset:
    """A first-class carrier preset definition.

    This is the SINGLE SOURCE OF TRUTH for carrier-level parameters.
    The API accepts `carrier_preset` as a top-level parameter (distinct
    from `payload_compression` / `compression_preset`).
    """
    id: CarrierPresetId
    label: str
    description: str
    modality: CarrierModality = CarrierModality.BOTH

    # Payload compression default for this carrier (can be overridden by
    # explicit `payload_compression` parameter at encode time).
    payload_compression_default: str = "NO_COMPRESSION"  # "NO_COMPRESSION" | "DEFLATE"

    # Transfer semantics
    supports_lossless_transfer: bool = False
    expects_downstream_reencode: bool = True

    # Image engine mapping (for JPEG covers)
    image_quality_factor: int = 85  # QF for the DCT-QIM engine
    image_derate: float = 0.6       # reliability derate at this QF

    # Video engine mapping
    video_crf: int = 23
    video_derate: float = 1.0       # video uses raw mid-band eligibility

    # LSB spatial engine (for PNG/BMP covers in lossless preset)
    lsb_bits_per_channel: int = 1   # max 3; 1 = maximum PSNR

    # Safety margin applied to advertised capacity (0.0 - 1.0)
    safety_margin_ratio: float = 0.05

    # Capacity model version this preset was calibrated against
    capacity_model_version: str = "1.0"

    # Human-readable warnings for the UI
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Preset Registry
# ---------------------------------------------------------------------------

CARRIER_PRESETS: Dict[CarrierPresetId, CarrierPreset] = {
    CarrierPresetId.CHAT_STANDARD: CarrierPreset(
        id=CarrierPresetId.CHAT_STANDARD,
        label="Chat standard",
        description="WhatsApp/Messenger default upload — aggressive recompression expected.",
        modality=CarrierModality.BOTH,
        payload_compression_default="DEFLATE",
        supports_lossless_transfer=False,
        expects_downstream_reencode=True,
        image_quality_factor=75,   # conservative QF for chat re-encode survival
        image_derate=0.4,          # heavy derate: only ~40% of eligible blocks used
        video_crf=28,
        video_derate=1.0,
        safety_margin_ratio=0.10,  # 10% margin for hostile recompression
        warnings=[
            "Survives: WhatsApp/Messenger default re-encode (~Q60-70)",
            "NOT guaranteed to survive: multiple re-encodes, social media transcodes",
            "Payload compression default: DEFLATE (chat standard)",
        ],
    ),
    CarrierPresetId.CHAT_HD: CarrierPreset(
        id=CarrierPresetId.CHAT_HD,
        label="Chat HD",
        description="WhatsApp/Messenger HD toggle — moderate recompression.",
        modality=CarrierModality.BOTH,
        payload_compression_default="DEFLATE",
        supports_lossless_transfer=False,
        expects_downstream_reencode=True,
        image_quality_factor=85,
        image_derate=0.6,
        video_crf=23,
        video_derate=1.0,
        safety_margin_ratio=0.07,
        warnings=[
            "Survives: WhatsApp/Messenger HD re-encode (~Q80-85)",
            "NOT guaranteed to survive: standard chat re-encode, multiple passes",
            "Payload compression default: DEFLATE (chat standard)",
        ],
    ),
    CarrierPresetId.LOSSLESS_HIGH_CAPACITY: CarrierPreset(
        id=CarrierPresetId.LOSSLESS_HIGH_CAPACITY,
        label="Lossless high capacity (Pendrive / LAN)",
        description=(
            "Maximum direct-extraction capacity for byte-preserving transfer. "
            "PNG/BMP covers use lossless LSB spatial embedding; JPEG covers use "
            "the highest DCT QF (95). Video uses CRF 18 (near-lossless). "
            "The generated file extracts perfectly when copied bit-for-bit "
            "(pendrive, local disk, LAN). It does NOT survive any lossy re-encode."
        ),
        modality=CarrierModality.BOTH,
        payload_compression_default="NO_COMPRESSION",
        supports_lossless_transfer=True,
        expects_downstream_reencode=False,
        image_quality_factor=95,
        image_derate=1.0,      # full reliability at QF 95
        video_crf=18,
        video_derate=1.0,
        lsb_bits_per_channel=1,
        safety_margin_ratio=0.0,  # no margin needed for direct extraction
        warnings=[
            "Survives: lossless round-trips (PNG/BMP re-save), NO lossy re-encode",
            "NOT guaranteed to survive: WhatsApp, Messenger, social media, any re-encode",
            "Payload compression default: NO COMPRESSION (max capacity)",
            "Video: CRF 18 (near-lossless) — extracts perfectly if NOT re-encoded",
        ],
    ),
}


def get_carrier_preset(preset_id: str | CarrierPresetId) -> CarrierPreset:
    """Resolve a carrier preset by ID string or enum."""
    if isinstance(preset_id, CarrierPresetId):
        return CARRIER_PRESETS[preset_id]
    try:
        return CARRIER_PRESETS[CarrierPresetId(preset_id)]
    except ValueError:
        raise ValueError(f"Unknown carrier preset: {preset_id}")


def list_carrier_presets(modality: Optional[CarrierModality] = None) -> List[CarrierPreset]:
    """List all carrier presets, optionally filtered by modality."""
    presets = list(CARRIER_PRESETS.values())
    if modality is not None:
        presets = [p for p in presets if p.modality == modality or p.modality == CarrierModality.BOTH]
    return presets


# ---------------------------------------------------------------------------
# Mapping helpers for the encode pipeline
# ---------------------------------------------------------------------------

def carrier_preset_to_image_qf(preset_id: CarrierPresetId) -> int:
    """Map a carrier preset to the image DCT-QIM quality factor."""
    return get_carrier_preset(preset_id).image_quality_factor


def carrier_preset_to_video_crf(preset_id: CarrierPresetId) -> int:
    """Map a carrier preset to the video engine CRF."""
    return get_carrier_preset(preset_id).video_crf


def carrier_preset_to_payload_compression_default(preset_id: CarrierPresetId) -> str:
    """Default payload compression for a carrier preset ('NO_COMPRESSION' | 'DEFLATE')."""
    return get_carrier_preset(preset_id).payload_compression_default


def carrier_preset_to_lsb_bpc(preset_id: CarrierPresetId) -> int:
    """Bits per channel for LSB spatial engine (lossless preset only)."""
    return get_carrier_preset(preset_id).lsb_bits_per_channel