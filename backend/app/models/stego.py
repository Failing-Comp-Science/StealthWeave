"""
Pydantic models for the stego API (request validation + OpenAPI schema).

Payload-type enum matches the HSTG v2 container (``modules.container.PayloadType``):
TEXT_MESSAGE, TEXT_FILE, IMAGE. Cover type is derived server-side from the
uploaded file.
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class CoverType(str, Enum):
    IMAGE = "IMAGE"
    VIDEO = "VIDEO"


class PayloadType(str, Enum):
    TEXT_MESSAGE = "TEXT_MESSAGE"
    TEXT_FILE = "TEXT_FILE"
    IMAGE = "IMAGE"


class CompressionPreset(str, Enum):
    """Channel-level compression presets exposed by the API.

    Mirrors ``modules.container.CompressionPreset`` (NO_COMPRESSION /
    CHAT_STANDARD / CHAT_HD). The container's per-carrier `CompressionPresetId`
    (light/standard/heavy) is a separate, orthogonal axis.
    """
    NO_COMPRESSION = "NO_COMPRESSION"
    CHAT_STANDARD = "CHAT_STANDARD"
    CHAT_HD = "CHAT_HD"


# Restricted cover/payload matrix enforced at the API layer (task step 5).
#   IMAGE cover -> {TEXT_MESSAGE, TEXT_FILE}
#   VIDEO cover -> {TEXT_MESSAGE, TEXT_FILE, IMAGE}
ALLOWED_PAYLOADS = {
    CoverType.IMAGE: [PayloadType.TEXT_MESSAGE, PayloadType.TEXT_FILE],
    CoverType.VIDEO: [PayloadType.TEXT_MESSAGE, PayloadType.TEXT_FILE, PayloadType.IMAGE],
}

# Tokens we recognize for validation. VIDEO is intentionally NOT a container
# payload type; it is listed only so a VIDEO-into-cover request produces a clear
# 400 (task: "Reject VIDEO payload into a VIDEO cover") instead of a 422.
KNOWN_PAYLOAD_TOKENS = {"TEXT_MESSAGE", "TEXT_FILE", "IMAGE", "VIDEO"}


class PresetCapacity(BaseModel):
    """Capacity for one compression preset.

    Image presets populate ``target_quality_factor`` +
    ``max_bytes_text_message`` / ``max_bytes_text_file``. Video presets populate
    ``target_crf`` + the per-minute rates and ``max_bytes_image``.

    ``compression_preset`` / ``text_compression_factor`` echo the channel-level
    compression preset the capacity was computed under (e.g. NO_COMPRESSION =>
    factor 1.0).
    """
    id: str
    name: str
    description: str
    technique: str
    expected_ber: float = Field(..., description="Modeled post-ECC BER at the preset target quality")
    survivability_description: str

    # Channel-level compression context (per-call, echoed into every carrier)
    compression_preset: Optional[str] = None
    text_compression_factor: Optional[float] = None

    # Image-only
    target_quality_factor: Optional[int] = None
    max_bytes_text_message: Optional[int] = None
    max_bytes_text_file: Optional[int] = None

    # Video-only
    target_crf: Optional[int] = None
    max_bytes_per_minute_text_message: Optional[int] = None
    max_bytes_per_minute_text_file: Optional[int] = None
    max_bytes_image: Optional[int] = None

    # Diagnostics (optional; useful for the evaluation harness)
    total_blocks: Optional[int] = None
    high_texture_blocks: Optional[int] = None
    usable_coeff_slots: Optional[int] = None
    iframes_total: Optional[int] = None
    iframes_per_minute: Optional[float] = None
    usable_coeff_slots_per_iframe: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    duration_sec: Optional[float] = None


class CapacityResponse(BaseModel):
    cover_type: CoverType
    payload_type: PayloadType
    compression_preset: CompressionPreset
    allowed_payload_types: List[PayloadType]
    container_version: int
    presets: List[PresetCapacity]


class DecodeResponse(BaseModel):
    """Recovered payload from a stego image."""
    payload_type: PayloadType
    container_version: int
    original_filename: Optional[str] = None
    mime_type: Optional[str] = None
    message: Optional[str] = None
    payload_base64: Optional[str] = None
    compressed: Optional[bool] = None


class ErrorResponse(BaseModel):
    detail: str
