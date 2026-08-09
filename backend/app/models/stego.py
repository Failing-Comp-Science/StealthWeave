"""
Pydantic models for the stego API (request validation + OpenAPI schema).

Payload-type enum matches the HSTG v2 container (``modules.container.PayloadType``):
TEXT_MESSAGE, TEXT_FILE, IMAGE. Cover type is derived server-side from the
uploaded file.

Carrier presets (NEW in Stage 2) are the first-class axis governing carrier
capacity and transfer semantics, independent of payload compression.
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
    CHAT_STANDARD / CHAT_HD). This is the PAYLOAD compression axis.
    """
    NO_COMPRESSION = "NO_COMPRESSION"
    CHAT_STANDARD = "CHAT_STANDARD"
    CHAT_HD = "CHAT_HD"


class CarrierPreset(str, Enum):
    """Carrier-level presets governing capacity and transfer semantics (Stage 2).

    Independent of payload compression. These are the first-class carrier presets:
    - CHAT_STANDARD: WhatsApp/Messenger default upload (survives aggressive re-encode)
    - CHAT_HD: WhatsApp/Messenger HD toggle (survives moderate re-encode)
    - LOSSLESS_HIGH_CAPACITY: Pendrive/LAN/disk transfer (direct extraction only)
    """
    CHAT_STANDARD = "chat_standard"
    CHAT_HD = "chat_hd"
    LOSSLESS_HIGH_CAPACITY = "lossless_high_capacity"


class PayloadCompression(str, Enum):
    """Payload compression mode (independent of carrier preset).

    NO_COMPRESSION: Raw payload -> RS-ECC -> AES-GCM (no DEFLATE)
    DEFLATE: Payload -> DEFLATE -> RS-ECC -> AES-GCM
    """
    NO_COMPRESSION = "NO_COMPRESSION"
    DEFLATE = "DEFLATE"


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

    # Itemized accounting breakdown (NEW in Stage 2)
    accounting: Optional[dict] = None


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
    """Structured API error.

    ``code`` is a stable, machine-readable token (see :class:`StegoErrorCode`)
    the frontend can branch on; ``detail`` is the human-readable message.
    ``detail`` alone is kept for backward compatibility with older clients that
    only read ``detail`` (FastAPI's default 400 shape).
    """
    detail: str
    code: Optional[str] = None


class StegoErrorCode(str, Enum):
    """Stable error codes surfaced by the stego endpoints (task Phase 2C).

    Grouped by stage so the frontend can decide whether an error is a
    user-recoverable validation problem (bad file / too large) or an
    engine/environment failure.
    """
    # --- cover / input validation ---
    COVER_TYPE_UNSUPPORTED = "COVER_TYPE_UNSUPPORTED"
    PAYLOAD_TYPE_INVALID = "PAYLOAD_TYPE_INVALID"
    PAYLOAD_COMBO_INVALID = "PAYLOAD_COMBO_INVALID"
    PRESET_INVALID = "PRESET_INVALID"
    UPLOAD_EMPTY = "UPLOAD_EMPTY"
    UPLOAD_TOO_LARGE = "UPLOAD_TOO_LARGE"

    # --- payload assembly ---
    PAYLOAD_MISSING = "PAYLOAD_MISSING"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"

    # --- image ---
    IMAGE_FILE_EMPTY = "IMAGE_FILE_EMPTY"
    IMAGE_FORMAT_UNSUPPORTED = "IMAGE_FORMAT_UNSUPPORTED"
    IMAGE_DECODE_FAILED = "IMAGE_DECODE_FAILED"
    IMAGE_CAPACITY_EXCEEDED = "IMAGE_CAPACITY_EXCEEDED"
    IMAGE_EMBED_FAILED = "IMAGE_EMBED_FAILED"
    IMAGE_NO_PAYLOAD_FOUND = "IMAGE_NO_PAYLOAD_FOUND"

    # --- video ---
    VIDEO_FILE_EMPTY = "VIDEO_FILE_EMPTY"
    VIDEO_FORMAT_UNSUPPORTED = "VIDEO_FORMAT_UNSUPPORTED"
    VIDEO_CODEC_UNSUPPORTED = "VIDEO_CODEC_UNSUPPORTED"
    VIDEO_PROBE_FAILED = "VIDEO_PROBE_FAILED"
    VIDEO_NO_USABLE_FRAMES = "VIDEO_NO_USABLE_FRAMES"
    VIDEO_NO_I_FRAMES = "VIDEO_NO_I_FRAMES"
    VIDEO_CAPACITY_EXCEEDED = "VIDEO_CAPACITY_EXCEEDED"
    VIDEO_PAYLOAD_TOO_LARGE = "VIDEO_PAYLOAD_TOO_LARGE"
    VIDEO_EMBED_FAILED = "VIDEO_EMBED_FAILED"
    VIDEO_OUTPUT_ENCODE_FAILED = "VIDEO_OUTPUT_ENCODE_FAILED"
    VIDEO_TEMPFILE_FAILED = "VIDEO_TEMPFILE_FAILED"

    # --- decode / recovery ---
    DECODE_NO_PAYLOAD = "DECODE_NO_PAYLOAD"
    DECODE_RECOVERY_FAILED = "DECODE_RECOVERY_FAILED"

    # --- catch-all ---
    INTERNAL_ERROR = "INTERNAL_ERROR"
