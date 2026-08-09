"""
Stego API router.

Endpoints:
    POST /api/stego/capacity?payload_type=...  (multipart: cover=<file>)
    POST /api/stego/encode                      (multipart: cover, payload, ...)
    POST /api/stego/decode                      (multipart: stego=<file>, password)

Encode/decode dispatch on cover type:

* IMAGE covers use the block-based DCT-QIM engine
  (``modules.capacity.dct_embedder``) for TEXT_MESSAGE / TEXT_FILE payloads.
* VIDEO covers use the I-frame DCT-QIM engine
  (``modules.video_stego.engine``) with an H.264 CRF re-encode, for
  TEXT_MESSAGE / TEXT_FILE / IMAGE payloads.

Both engines embed the HSTG v2 container (``modules.container``).

NOTE: implemented as POST rather than the prompt's "GET" because a browser
cannot attach a multipart file body to a GET request via fetch/XHR. The path,
the ``payload_type`` param, and the restricted-matrix semantics are as
specified. Invalid cover/payload combinations return HTTP 400 with a clear
message the UI surfaces via Toast/Alert.

SECURITY: this router is unauthenticated (there is no auth anywhere in the repo
yet — audit §2/§4). Encode/decode are stateless (no persistence), but any
deployment exposing them publicly should add auth + upload limits at the edge.
"""
from __future__ import annotations

import base64
import io
import os
import tempfile
from typing import Optional

import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile
from PIL import Image

from modules.base import HEADER_VERSION_V2
from modules.capacity import (
    AccountingBreakdown,
    compute_accounting_breakdown,
    image_capacity,
    spatial_capacity,
    video_capacity,
    carrier_preset_to_image_qf,
    carrier_preset_to_video_crf,
    carrier_preset_to_payload_compression_default,
    carrier_preset_to_lsb_bpc,
    get_carrier_preset,
)
from modules.capacity.accounting import spatial_container_budget
from modules.capacity.dct_embedder import CapacityError, encode_jpeg, extract_payload
from modules.capacity.image_capacity import dct_eligible_bits
from modules.capacity.presets import IMAGE_PRESETS, VIDEO_PRESETS
from modules.capacity.unified_presets import (
    UnifiedPresetId,
    get_unified_preset,
    is_unified_preset_token,
    legacy_engine_tier_to_unified,
    resolve_preset as resolve_unified_preset,
    unified_to_container_preset,
)
from modules.capacity.video_capacity import VideoProbeError
from modules.image_stego.lsb import LSBEmbedder
from modules.metrics import psnr, ssim
from modules.video_stego import (
    VideoCapacityError,
    VideoEmbedError,
    VideoNoIFramesError,
    embed_video,
    extract_video,
)
from modules.video_stego._codec import video_psnr
from modules.container import (
    CompressionPreset as ContainerCompressionPreset,
    CompressionPresetId,
    PayloadType as ContainerPayloadType,
    build_container,
    parse_container,
)

from app.core.errors import StegoError
from app.models.stego import (
    ALLOWED_PAYLOADS,
    KNOWN_PAYLOAD_TOKENS,
    CapacityResponse,
    CarrierPreset,
    CompressionPreset,
    CoverType,
    DecodeResponse,
    ErrorResponse,
    PayloadCompression,
    PayloadType,
    PresetCapacity,
    StegoErrorCode,
)

router = APIRouter(prefix="/stego", tags=["stego"])

# Reject absurdly large uploads early (defense-in-depth; tune per deployment).
MAX_UPLOAD_BYTES = 256 * 1024 * 1024  # 256 MiB

# Video PSNR compares this many leading frames (bounds per-encode cost).
VIDEO_PSNR_MAX_FRAMES = 60

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")
_VIDEO_EXTS = (".mp4", ".webm", ".mov", ".mkv", ".ogv", ".avi", ".m4v")

#: Preset id -> JPEG quality factor the DCT-QIM engine embeds at
#: (mirrors ``presets.IMAGE_PRESETS``).
_PRESET_QF = {p.id: p.target_quality_factor for p in IMAGE_PRESETS}

#: API channel-preset token -> container-level CompressionPreset.
_API_TO_CONTAINER_PRESET = {
    CompressionPreset.NO_COMPRESSION: ContainerCompressionPreset.NO_COMPRESSION,
    CompressionPreset.CHAT_STANDARD: ContainerCompressionPreset.CHAT_STANDARD,
    CompressionPreset.CHAT_HD: ContainerCompressionPreset.CHAT_HD,
}


def _resolve_container_preset(api_preset: CompressionPreset, compress: bool) -> ContainerCompressionPreset:
    """Map the UI's channel preset (or legacy ``compress`` bool) to a container preset.

    Backward compatibility: when the client sends only ``compress=true``, treat
    it as CHAT_STANDARD; ``compress=false`` (or an explicit NO_COMPRESSION
    selection) maps to NO_COMPRESSION. When a real channel preset is given it
    wins over the legacy boolean.
    """
    if api_preset == CompressionPreset.NO_COMPRESSION and compress:
        return ContainerCompressionPreset.CHAT_STANDARD
    return _API_TO_CONTAINER_PRESET[api_preset]


#: The single user-facing preset axis. The UI sends ONE ``preset`` id; all
#: legacy parameters (carrier_preset / compression_preset / compress / bare
#: light|standard|heavy tokens) remain accepted for backward compatibility and
#: resolve onto the same axis (see unified_presets.resolve_preset).
_UNIFIED_PRESET_IDS = {
    pid.value for pid in UnifiedPresetId
}


def _annotate_unified(row: dict) -> dict:
    """Attach the unified preset id/label to a capacity-model row.

    Engine-tier rows (light/standard/heavy) map 1:1 onto the unified ids; the
    lossless spatial row (PNG/BMP covers) is the LOSSLESS preset.
    """
    tier = row.get("id")
    if tier == "lossless_high_capacity":
        pid = UnifiedPresetId.LOSSLESS
    else:
        try:
            pid = legacy_engine_tier_to_unified(tier)
        except ValueError:
            return row
    enriched = dict(row)
    enriched["preset_id"] = pid.value
    enriched["preset_label"] = get_unified_preset(pid).label
    return enriched


def _container_preset_from_unified(pid: UnifiedPresetId) -> ContainerCompressionPreset:
    """Channel-level compression preset the capacity model should use for a
    unified preset (TEXT_FILE compression multiplier)."""
    return unified_to_container_preset(pid)


def _is_lossless_alias(token: str) -> bool:
    """True for the pre-rename ids that map onto the LOSSLESS preset.

    ``LOCAL_HIGH_CAPACITY`` (pre-rename canonical id) and the Stage-2
    ``LOSSLESS_HIGH_CAPACITY`` resolve onto the unified LOSSLESS axis so old
    clients keep working; both echo ``LOSSLESS`` in the API.
    """
    return (token or "").strip().upper() in {"LOCAL_HIGH_CAPACITY", "LOSSLESS_HIGH_CAPACITY"}


def _resolve_requested_capacity_preset(
    preset: str, compression_preset: CompressionPreset
) -> tuple[Optional[UnifiedPresetId], ContainerCompressionPreset]:
    """Resolve the capacity endpoint's preset axis.

    The unified ``preset`` query param is the first-class axis (default
    LOSSLESS). A legacy ``compression_preset`` explicitly sent by
    old clients (anything other than the NO_COMPRESSION default) wins over it
    so legacy callers keep their compression semantics; NO_COMPRESSION maps to
    the same factor (1.0) as LOSSLESS, so there is no conflict.
    Returns ``(unified_id_or_None, container_preset)``.
    """
    if compression_preset != CompressionPreset.NO_COMPRESSION:
        return None, _resolve_container_preset(compression_preset, compress=False)
    if is_unified_preset_token(preset) or _is_lossless_alias(preset):
        pid = get_unified_preset(preset).id
        return pid, _container_preset_from_unified(pid)
    return None, _resolve_container_preset(compression_preset, compress=False)


def _detect_cover_type(upload: UploadFile) -> CoverType:
    content_type = (upload.content_type or "").lower()
    name = (upload.filename or "").lower()
    if content_type.startswith("image/") or name.endswith(_IMAGE_EXTS):
        return CoverType.IMAGE
    if content_type.startswith("video/") or name.endswith(_VIDEO_EXTS):
        return CoverType.VIDEO
    raise StegoError(
        StegoErrorCode.COVER_TYPE_UNSUPPORTED,
        "Unsupported cover type. Upload an image (PNG/JPG/WebP/BMP) or a "
        "video (MP4/WebM/MOV).",
    )


def _validate_combo(cover_type: CoverType, raw_payload_type: str) -> PayloadType:
    token = (raw_payload_type or "").strip().upper()
    if token not in KNOWN_PAYLOAD_TOKENS:
        raise StegoError(
            StegoErrorCode.PAYLOAD_TYPE_INVALID,
            f"Unknown payload_type '{raw_payload_type}'. Expected one of "
            "TEXT_MESSAGE, TEXT_FILE, IMAGE.",
        )
    allowed = ALLOWED_PAYLOADS[cover_type]
    allowed_names = [p.value for p in allowed]
    if token not in allowed_names:
        raise StegoError(
            StegoErrorCode.PAYLOAD_COMBO_INVALID,
            f"{token} payload is not allowed in a {cover_type.value} cover. "
            f"Allowed payload types for this cover: {', '.join(allowed_names)}.",
        )
    return PayloadType(token)


def _decode_image(data: bytes) -> np.ndarray:
    try:
        with Image.open(io.BytesIO(data)) as img:
            return np.asarray(img.convert("RGB"))
    except Exception as exc:  # noqa: BLE001 - surface as a clean 400
        raise StegoError(
            StegoErrorCode.IMAGE_DECODE_FAILED, f"Could not decode image: {exc}"
        )


def _stego_headers(
    stats,
    crf: Optional[int] = None,
    psnr_db: Optional[float] = None,
    ssim_val: Optional[float] = None,
    ber_val: Optional[float] = None,
    container_bytes: Optional[int] = None,
    preset_id: Optional[str] = None,
) -> dict:
    """X-Stego-* response headers shared by the encode endpoints."""
    headers = {
        "X-Stego-Iterations": str(stats.iters),
        "X-Stego-Residual-Bits": str(stats.residual_bit_errors),
        "X-Stego-Blocks-Eligible": str(stats.blocks_eligible),
        "X-Stego-Blocks-Used": str(stats.blocks_used),
    }
    if psnr_db is not None:
        headers["X-Stego-PSNR"] = f"{psnr_db:.2f}"
    if ssim_val is not None:
        headers["X-Stego-SSIM"] = f"{ssim_val:.4f}"
    if ber_val is not None:
        headers["X-Stego-BER"] = f"{ber_val:.6f}"
    if crf is not None:
        headers["X-Stego-CRF"] = str(crf)
    if container_bytes is not None:
        headers["X-Stego-Container-Bytes"] = str(int(container_bytes))
    if preset_id is not None:
        headers["X-Stego-Preset"] = preset_id
    return headers


def _bit_error_rate(stats) -> float:
    """Residual channel bit errors over embedded payload bits (0 when empty)."""
    if not getattr(stats, "payload_bits", None):
        return 0.0
    return float(stats.residual_bit_errors) / float(stats.payload_bits)


def _decode_image_safe(data: bytes) -> Optional[np.ndarray]:
    """Best-effort RGB decode used only for metric computation."""
    try:
        return _decode_image(data)
    except (StegoError, HTTPException):
        return None


def _preflight_container_bits(container_len: int) -> int:
    """Embeddable bits the DCT-QIM pipeline needs for a serialized container.

    Mirrors the REAL chain (channel RS(255,223) + 128-bit framing prefix) via
    the authoritative accounting module, so the endpoint can perform an EXACT
    fit check against measured carrier capacity BEFORE embedding / re-encoding.
    """
    from modules.capacity.accounting import required_bits_for_container

    return required_bits_for_container(container_len)


_SUPPORTED_VIDEO_CODECS = {"h264", "avc1", "hevc", "h265", "vp8", "vp9", "av1", "mpeg4", "mpeg4video"}


def _validate_upload(data: bytes, *, is_video: bool) -> None:
    """Reject empty / oversized uploads with a stable code."""
    empty = StegoErrorCode.VIDEO_FILE_EMPTY if is_video else StegoErrorCode.IMAGE_FILE_EMPTY
    if not data:
        raise StegoError(empty, "Empty cover upload.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise StegoError(StegoErrorCode.UPLOAD_TOO_LARGE, "Cover file too large.")


def _map_video_error(exc: Exception) -> StegoError:
    """Map a video engine/probe exception to a structured StegoError.

    Fine-grained so the frontend can tell a user-recoverable capacity problem
    (fail BEFORE any output) from a probe/codec/engine failure.
    """
    if isinstance(exc, VideoCapacityError):
        return StegoError(StegoErrorCode.VIDEO_CAPACITY_EXCEEDED, str(exc))
    if isinstance(exc, VideoNoIFramesError):
        return StegoError(StegoErrorCode.VIDEO_NO_I_FRAMES, str(exc))
    if isinstance(exc, VideoProbeError):
        return StegoError(StegoErrorCode.VIDEO_PROBE_FAILED, f"Could not read video: {exc}")
    if isinstance(exc, VideoEmbedError):
        return StegoError(StegoErrorCode.VIDEO_EMBED_FAILED, str(exc))
    return StegoError(StegoErrorCode.VIDEO_EMBED_FAILED, f"Video processing failed: {exc}")


def _probe_video_or_raise(path: str) -> dict:
    """Probe a cover video (PyAV) and validate it is usable for embedding.

    Returns a NON-SENSITIVE diagnostic dict (codec/pixfmt/dims/fps/frame count/
    keyframes). Raises structured errors for empty/formatless/codec-unsupported/
    no-I-frame inputs so the endpoint fails cleanly BEFORE building a container.
    """
    try:
        from modules.video_stego._codec import probe_video

        width, height, fps, nb_frames, keyframes = probe_video(path)
    except Exception as exc:  # noqa: BLE001 - PyAV open/decode failure
        raise StegoError(
            StegoErrorCode.VIDEO_PROBE_FAILED,
            f"Could not read this video (probe failed): {exc}",
        )
    if nb_frames <= 0 or width <= 0 or height <= 0:
        raise StegoError(
            StegoErrorCode.VIDEO_NO_USABLE_FRAMES,
            "Video has no decodable frames.",
        )
    if not keyframes:
        raise StegoError(
            StegoErrorCode.VIDEO_NO_I_FRAMES,
            "Video exposes no I-frames to embed into. Re-encode with a regular "
            "GOP (keyframe interval) and try again.",
        )
    return {
        "width": width, "height": height, "fps": fps,
        "frame_count": nb_frames, "keyframe_count": len(keyframes),
    }


def _image_metric_headers(
    stats, cover_rgb: np.ndarray, stego_bytes: bytes,
    container_bytes: Optional[int] = None,
) -> dict:
    """X-Stego-* headers including per-encode PSNR/SSIM/BER for image covers."""
    stego_rgb = _decode_image_safe(stego_bytes)
    psnr_db = ssim_val = None
    if stego_rgb is not None and cover_rgb.shape == stego_rgb.shape:
        psnr_db = psnr(cover_rgb, stego_rgb)
        ssim_val = ssim(cover_rgb, stego_rgb)
    return _stego_headers(
        stats, psnr_db=psnr_db, ssim_val=ssim_val,
        ber_val=_bit_error_rate(stats), container_bytes=container_bytes,
    )


def _lsb_metric_headers(cover_rgb: np.ndarray, stego_png: bytes, container_bytes: int) -> dict:
    """X-Stego-* headers for the lossless LSB path (BER = 0 by construction)."""
    stego_rgb = _decode_image_safe(stego_png)
    psnr_db = ssim_val = None
    if stego_rgb is not None and cover_rgb.shape == stego_rgb.shape:
        psnr_db = psnr(cover_rgb, stego_rgb)
        ssim_val = ssim(cover_rgb, stego_rgb)
    return {
        "X-Stego-PSNR": f"{psnr_db:.2f}" if psnr_db is not None else "inf",
        "X-Stego-SSIM": f"{ssim_val:.4f}" if ssim_val is not None else "1.0",
        "X-Stego-BER": "0.0",
        "X-Stego-Container-Bytes": str(int(container_bytes)),
    }


def _decode_response(header: ContainerHeaderV2, payload: bytes) -> DecodeResponse:
    """Build the common DecodeResponse from a parsed container header."""
    if header.payload_type == ContainerPayloadType.TEXT_MESSAGE:
        return DecodeResponse(
            payload_type=PayloadType.TEXT_MESSAGE,
            container_version=HEADER_VERSION_V2,
            message=payload.decode("utf-8", errors="replace"),
            compressed=header.compressed,
        )
    return DecodeResponse(
        payload_type=PayloadType.IMAGE
        if header.payload_type == ContainerPayloadType.IMAGE
        else PayloadType.TEXT_FILE,
        container_version=HEADER_VERSION_V2,
        original_filename=header.original_filename,
        mime_type=header.mime_type,
        payload_base64=base64.b64encode(payload).decode("ascii"),
        compressed=header.compressed,
    )


@router.post(
    "/capacity",
    response_model=CapacityResponse,
    responses={400: {"model": ErrorResponse}},
    summary="Compute payload capacity for a cover across all compression presets",
)
async def stego_capacity(
    payload_type: str = Query(..., description="TEXT_MESSAGE | TEXT_FILE | IMAGE"),
    preset: str = Query(
        "LOSSLESS",
        description="Unified carrier preset (LOSSLESS | CHAT_STANDARD | CHAT_HD)",
    ),
    compression_preset: CompressionPreset = Query(
        CompressionPreset.NO_COMPRESSION,
        description="LEGACY: Channel compression preset (NO_COMPRESSION | CHAT_STANDARD | CHAT_HD)",
    ),
    cover: UploadFile = File(..., description="Cover image or video"),
) -> CapacityResponse:
    cover_type = _detect_cover_type(cover)
    validated_payload = _validate_combo(cover_type, payload_type)

    data = await cover.read()
    _validate_upload(data, is_video=cover_type == CoverType.VIDEO)

    unified_preset, container_preset = _resolve_requested_capacity_preset(
        preset, compression_preset
    )

    if cover_type == CoverType.IMAGE:
        rgb = _decode_image(data)
        # PNG/BMP covers ride the lossless spatial (LSB) engine, whose capacity
        # is orders of magnitude larger than the JPEG model's claim for the
        # same cover; JPEG (and other formats) keep the block DCT-QIM model.
        engine = _detect_image_engine_lenient(data)
        if engine == "lsb":
            presets = spatial_capacity(rgb, compression_preset=container_preset)
        else:
            presets = image_capacity(rgb, compression_preset=container_preset)
    else:
        suffix = os.path.splitext(cover.filename or "")[1] or ".mp4"
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(data)
                tmp_path = tmp.name
            presets = video_capacity(tmp_path, compression_preset=container_preset)
        except VideoProbeError as exc:
            raise StegoError(StegoErrorCode.VIDEO_PROBE_FAILED, f"Could not read video: {exc}")
        except StegoError:
            raise
        except Exception as exc:  # noqa: BLE001 - probe/model failure -> structured 400
            raise StegoError(
                StegoErrorCode.VIDEO_PROBE_FAILED,
                f"Could not compute video capacity: {exc}",
            )
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    return CapacityResponse(
        cover_type=cover_type,
        payload_type=validated_payload,
        compression_preset=compression_preset,
        preset=unified_preset.value if unified_preset else None,
        allowed_payload_types=ALLOWED_PAYLOADS[cover_type],
        container_version=HEADER_VERSION_V2,
        presets=[
            _preset_with_accounting(
                _annotate_unified(p), cover_type, container_preset, validated_payload
            )
            for p in presets
        ],
    )


def _preset_with_accounting(
    preset: dict, cover_type: CoverType, container_preset, payload_type: PayloadType
) -> PresetCapacity:
    """Enrich a preset dict with an itemized accounting breakdown.

    The accounting is computed for a representative payload at the preset's
    advertised capacity (TEXT_MESSAGE), so the UI can show an itemized
    breakdown of where every byte goes.
    """
    try:
        from modules.capacity.accounting import compute_accounting_breakdown
        from modules.container import container_overhead_bytes

        # Determine which payload type to use for the accounting example
        # Use TEXT_MESSAGE as it's the simplest (no filename/mime overhead)
        overhead = container_overhead_bytes(use_ecc=True, encrypted=True)

        # Get the max payload for TEXT_MESSAGE at this preset
        if cover_type == CoverType.IMAGE:
            max_payload = preset.get("max_bytes_text_message", 0)
            if max_payload is None or max_payload <= 0:
                return PresetCapacity(**preset)
            # For image DCT, available bits = eligible_blocks
            available_bits = preset.get("eligible_blocks", 0)
            if available_bits <= 0:
                return PresetCapacity(**preset)
        else:
            max_payload = preset.get("max_bytes_per_minute_text_message", 0)
            if max_payload is None or max_payload <= 0:
                return PresetCapacity(**preset)
            # Video: total available bits across the clip
            available_bits = preset.get("usable_coeff_slots_per_iframe", 0) * preset.get("iframes_total", 0)
            if available_bits <= 0:
                return PresetCapacity(**preset)

        if max_payload <= 0 or available_bits <= 0:
            return PresetCapacity(**preset)

        # Use the channel preset's text compression factor
        ratio = getattr(container_preset, 'text_compression_factor', 1.0)

        breakdown = compute_accounting_breakdown(
            payload_bytes=max_payload,
            fixed_overhead=overhead,
            ratio=ratio,
            available_bits=available_bits,
            exact=True,
        )

        enriched = dict(preset)
        enriched["accounting"] = breakdown.__dict__
        return PresetCapacity(**enriched)
    except Exception:
        # If anything goes wrong with accounting, return the preset without it
        return PresetCapacity(**preset)


# ---------------------------------------------------------------------------
# Encode / decode (block-based DCT-QIM engine)
# ---------------------------------------------------------------------------

def _resolve_preset(raw: str) -> int:
    """Preset id -> quality factor, or a bare quality factor (1..100)."""
    token = (raw or "light").strip().lower()
    if token in _PRESET_QF:
        return _PRESET_QF[token]
    try:
        qf = int(token)
    except ValueError:
        raise StegoError(
            StegoErrorCode.PRESET_INVALID,
            f"Unknown preset '{raw}'. Expected light | standard | heavy or a quality factor 1-100.",
        )
    if not 1 <= qf <= 100:
        raise StegoError(StegoErrorCode.PRESET_INVALID, "Quality factor must be in 1..100.")
    return qf


def _resolve_video_preset(raw: str) -> int:
    """Video preset id -> CRF, or a bare CRF (18..32)."""
    token = (raw or "standard").strip().lower()
    video = next((p for p in VIDEO_PRESETS if p.id == token), None)
    if video is not None:
        return int(video.target_crf)
    try:
        crf = int(token)
    except ValueError:
        raise StegoError(
            StegoErrorCode.PRESET_INVALID,
            f"Unknown preset '{raw}'. Expected light | standard | heavy or a CRF 18-32.",
        )
    if not 18 <= crf <= 32:
        raise StegoError(StegoErrorCode.PRESET_INVALID, "CRF must be in 18..32.")
    return crf


class _EffectiveEncodeParams:
    """Complete engine configuration resolved for ONE encode request.

    The unified ``preset`` is the single axis; legacy parameters are resolved
    onto the same shape so every path embeds with identical semantics.
    """

    __slots__ = (
        "preset_id", "preset_label", "qf", "crf", "delta", "bpc",
        "payload_comp", "container_tier_id",
    )

    def __init__(
        self,
        *,
        preset_id: Optional[UnifiedPresetId],
        qf: int, crf: int, delta: float, bpc: int,
        payload_comp: str,
        container_tier_id: CompressionPresetId,
    ):
        self.preset_id = preset_id
        self.preset_label = get_unified_preset(preset_id).label if preset_id else None
        self.qf = qf
        self.crf = crf
        self.delta = delta
        self.bpc = bpc
        self.payload_comp = payload_comp
        self.container_tier_id = container_tier_id


def _resolve_effective_encode_params(
    *,
    cover_type: CoverType,
    carrier_format: str,
    payload_type: PayloadType,
    preset_token: str,
    carrier_preset: CarrierPreset,
    payload_compression: Optional[PayloadCompression],
    compress: bool,
) -> _EffectiveEncodeParams:
    """Resolve the single preset axis for an encode request.

    Precedence (locked by tests):
      1. explicit unified ``preset`` id (LOSSLESS | CHAT_STANDARD |
         CHAT_HD) -> complete resolved configuration;
      2. legacy ``carrier_preset`` (chat_standard | chat_hd |
         lossless_high_capacity) when it is an explicit non-default choice;
      3. legacy ``preset`` token (light | standard | heavy, or a bare QF/CRF)
         for old clients;
    then the payload-compression ladder: explicit ``payload_compression`` wins
    over the legacy ``compress`` flag wins over the preset's default.
    """
    is_image = cover_type == CoverType.IMAGE

    if is_unified_preset_token(preset_token) or _is_lossless_alias(preset_token):
        cfg = resolve_unified_preset(
            preset_token, "image" if is_image else "video",
            carrier_format, payload_type.value,
        )
        return _EffectiveEncodeParams(
            preset_id=cfg.preset_id,
            qf=cfg.jpeg_quality if is_image else 0,
            crf=cfg.video_crf if not is_image else 0,
            delta=cfg.qim_strength,
            bpc=cfg.bits_per_channel,
            payload_comp="DEFLATE",  # deflate_if_smaller policy; overridden below
            container_tier_id=CompressionPresetId[cfg.engine_tier.upper()],
        )

    if carrier_preset != CarrierPreset.CHAT_STANDARD:
        # Legacy carrier preset axis (explicit non-default choice).
        cp = get_carrier_preset(carrier_preset)
        qf = cp.image_quality_factor if is_image else 0
        crf = cp.video_crf if not is_image else 0
        delta = 2.0 if qf >= 90 else 1.0
        tier = (_carrier_preset_for_qf(qf) if is_image else _carrier_preset_for_crf(crf))
        return _EffectiveEncodeParams(
            preset_id=None,
            qf=qf, crf=crf, delta=delta, bpc=cp.lsb_bits_per_channel,
            payload_comp=cp.payload_compression_default,
            container_tier_id=tier,
        )

    # Legacy preset token path (old clients): light | standard | heavy, or a
    # bare quality factor / CRF.
    if is_image:
        qf = _resolve_preset(preset_token)
        crf = 0
        tier = _carrier_preset_for_qf(qf)
        delta = 2.0 if qf >= 90 else 1.0
    else:
        crf = _resolve_video_preset(preset_token)
        qf = 0
        tier = _carrier_preset_for_crf(crf)
        delta = 2.0
    return _EffectiveEncodeParams(
        preset_id=None,
        qf=qf, crf=crf, delta=delta,
        bpc=carrier_preset_to_lsb_bpc(CarrierPreset.CHAT_STANDARD),
        payload_comp=None,  # legacy callers keep the compress-flag semantics
        container_tier_id=tier,
    )


def _apply_payload_compression(
    params: _EffectiveEncodeParams,
    payload_compression: Optional[PayloadCompression],
    compress: bool,
) -> str:
    """Payload-compression ladder: explicit param > legacy compress flag >
    the preset's default."""
    if payload_compression is not None:
        return (
            "DEFLATE"
            if payload_compression == PayloadCompression.DEFLATE
            else "NO_COMPRESSION"
        )
    if compress:
        return "DEFLATE"
    if params.payload_comp is None:
        return "NO_COMPRESSION"
    return params.payload_comp


async def _assemble_payload(
    validated: PayloadType,
    message: str,
    payload_file: Optional[UploadFile],
    payload_image: Optional[UploadFile],
) -> tuple[bytes, ContainerPayloadType, str, str]:
    """Read + validate the payload fields; return (bytes, container type, fname, mime)."""
    if validated == PayloadType.TEXT_MESSAGE:
        if not message:
            raise StegoError(StegoErrorCode.PAYLOAD_MISSING, "TEXT_MESSAGE payload requires a 'message' field.")
        return message.encode("utf-8"), ContainerPayloadType.TEXT_MESSAGE, "", ""
    if validated == PayloadType.TEXT_FILE:
        if payload_file is None or not (payload_file.filename or "").strip():
            raise StegoError(StegoErrorCode.PAYLOAD_MISSING, "TEXT_FILE payload requires a 'payload_file' upload.")
        payload_bytes = await payload_file.read()
        if not payload_bytes:
            raise StegoError(StegoErrorCode.PAYLOAD_MISSING, "Empty payload file.")
        fname = os.path.basename(payload_file.filename or "payload")
        mime = payload_file.content_type or "application/octet-stream"
        return payload_bytes, ContainerPayloadType.TEXT_FILE, fname, mime
    # IMAGE payload (video covers only; the _validate_combo matrix rejects it elsewhere)
    if payload_image is None or not (payload_image.filename or "").strip():
        raise StegoError(StegoErrorCode.PAYLOAD_MISSING, "IMAGE payload requires a 'payload_image' upload.")
    payload_bytes = await payload_image.read()
    if not payload_bytes:
        raise StegoError(StegoErrorCode.PAYLOAD_MISSING, "Empty payload image.")
    fname = os.path.basename(payload_image.filename or "payload.png")
    mime = payload_image.content_type or "image/png"
    return payload_bytes, ContainerPayloadType.IMAGE, fname, mime


# ---------------------------------------------------------------------------
# Shared encode paths (exact pre-embed fit check + structured errors)
# ---------------------------------------------------------------------------

def _carrier_preset_for_qf(qf: int) -> CompressionPresetId:
    return CompressionPresetId.LIGHT if qf >= 90 else (
        CompressionPresetId.STANDARD if qf >= 80 else CompressionPresetId.HEAVY
    )


def _carrier_preset_for_crf(crf: int) -> CompressionPresetId:
    return (
        CompressionPresetId.LIGHT if crf <= 20 else
        CompressionPresetId.STANDARD if crf <= 25 else CompressionPresetId.HEAVY
    )


def _encode_video(
    data: bytes, cover_filename: str, container: bytes, crf: int, password: str,
) -> tuple[bytes, object, Optional[float]]:
    """Embed a prebuilt container into a video cover with an EXACT fit check.

    Probes the cover (structured errors for empty/no-I-frame/probe failures),
    verifies the serialized container fits the measured I-frame carrier pool
    BEFORE any embed/re-encode, then runs the engine. Returns
    ``(stego_bytes, stats, psnr_db)``. Temp files are always cleaned up.
    """
    suffix = os.path.splitext(cover_filename or "")[1] or ".mp4"
    tmp_cover = None
    tmp_stego = None
    try:
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(data)
                tmp_cover = tmp.name
        except OSError as exc:
            raise StegoError(StegoErrorCode.VIDEO_TEMPFILE_FAILED, f"Could not buffer the video: {exc}")

        # Probe + validate BEFORE building/embedding (fail fast, no output).
        _probe_video_or_raise(tmp_cover)

        stego_bytes, stats = embed_video(tmp_cover, container, crf, password or None)
        if not stego_bytes:
            raise StegoError(StegoErrorCode.VIDEO_EMBED_FAILED, "Embedding produced no output.")

        psnr_db = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(stego_bytes)
                tmp_stego = tmp.name
            psnr_db = video_psnr(tmp_cover, tmp_stego, max_frames=VIDEO_PSNR_MAX_FRAMES)
        except (ValueError, OSError):
            psnr_db = None
        return stego_bytes, stats, psnr_db
    except StegoError:
        raise
    except (VideoEmbedError, VideoProbeError) as exc:
        raise _map_video_error(exc)
    except Exception as exc:  # noqa: BLE001 - never leak a 500 from the video path
        raise StegoError(StegoErrorCode.VIDEO_EMBED_FAILED, f"Video embedding failed: {exc}")
    finally:
        if tmp_cover and os.path.exists(tmp_cover):
            os.unlink(tmp_cover)
        if tmp_stego and os.path.exists(tmp_stego):
            os.unlink(tmp_stego)


def _encode_jpeg(rgb: np.ndarray, container: bytes, qf: int, delta: float = 1.0) -> tuple[bytes, object]:
    """Embed a prebuilt container into a JPEG cover with an EXACT fit check.

    Compares the serialized container's exact channel-bit demand against the
    cover's measured DCT-QIM carrier capacity (same derate the capacity model
    advertises) and rejects with ``IMAGE_CAPACITY_EXCEEDED`` BEFORE embedding.
    ``delta`` is the QIM quantization step (higher = more margin, coarser).
    """
    available = dct_eligible_bits(rgb, qf)
    needed = _preflight_container_bits(len(container))
    if needed > available:
        raise StegoError(
            StegoErrorCode.IMAGE_CAPACITY_EXCEEDED,
            f"Payload does not fit this cover at quality {qf}: needs "
            f"{needed} carrier bits, cover offers {available}. Use a larger "
            "cover, a higher-capacity preset, or a smaller/compressed payload.",
        )
    try:
        return encode_jpeg(rgb, container, qf, delta=delta)
    except CapacityError as exc:
        raise StegoError(StegoErrorCode.IMAGE_CAPACITY_EXCEEDED, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise StegoError(StegoErrorCode.IMAGE_EMBED_FAILED, f"Image embedding failed: {exc}")


@router.post(
    "/encode",
    responses={400: {"model": ErrorResponse}},
    summary="Embed a payload into a cover image or video (DCT-QIM)",
)
async def stego_encode(
    cover: UploadFile = File(..., description="Cover image or video"),
    payload_type: str = Form("TEXT_MESSAGE", description="TEXT_MESSAGE | TEXT_FILE | IMAGE"),
    # PRIMARY: the single user-facing preset axis (unified presets). Legacy
    # tokens (light | standard | heavy, or a bare QF / CRF) are still accepted
    # in this field for old clients.
    preset: str = Form(
        "LOSSLESS",
        description="Unified preset: LOSSLESS | CHAT_STANDARD | CHAT_HD (legacy: light | standard | heavy)",
    ),
    # LEGACY: kept for backward compatibility (see _resolve_effective_encode_params)
    carrier_preset: CarrierPreset = Form(
        CarrierPreset.CHAT_STANDARD,
        description="LEGACY: Carrier preset: chat_standard | chat_hd | lossless_high_capacity",
    ),
    payload_compression: Optional[PayloadCompression] = Form(
        None,
        description="LEGACY: Payload compression: NO_COMPRESSION | DEFLATE (explicit choice wins over the preset default)",
    ),
    password: str = Form("", description="Optional encryption password"),
    compress: bool = Form(False, description="LEGACY: Apply DEFLATE (legacy; use payload_compression)"),
    compression_preset: CompressionPreset = Form(
        CompressionPreset.NO_COMPRESSION,
        description="LEGACY: Channel compression preset (NO_COMPRESSION | CHAT_STANDARD | CHAT_HD)",
    ),
    message: str = Form("", description="Payload text (TEXT_MESSAGE)"),
    payload_file: Optional[UploadFile] = File(None, description="Payload file (TEXT_FILE)"),
    payload_image: Optional[UploadFile] = File(None, description="Payload image (IMAGE, video covers only)"),
) -> Response:
    cover_type = _detect_cover_type(cover)
    validated = _validate_combo(cover_type, payload_type)

    data = await cover.read()
    _validate_upload(data, is_video=cover_type == CoverType.VIDEO)

    # ---- Resolve the single preset axis (unified, with legacy fallbacks) ---
    params = _resolve_effective_encode_params(
        cover_type=cover_type,
        carrier_format=(os.path.splitext(cover.filename or "")[1] or "").lstrip("."),
        payload_type=validated,
        preset_token=preset,
        carrier_preset=carrier_preset,
        payload_compression=payload_compression,
        compress=compress,
    )
    payload_comp = _apply_payload_compression(params, payload_compression, compress)

    # ---- Assemble the HSTG v2 container payload ---------------------------
    payload_bytes, container_type, fname, mime = await _assemble_payload(
        validated, message, payload_file, payload_image
    )

    if cover_type == CoverType.VIDEO:
        # ---- VIDEO cover (I-frame DCT-QIM + H.264 CRF re-encode) ----------
        container = build_container(
            payload_bytes, container_type,
            compression_preset=params.container_tier_id, password=password or None,
            original_filename=fname, mime_type=mime,
            compress=(payload_comp == "DEFLATE"), use_ecc=True,
        )
        stego_bytes, stats, psnr_db = _encode_video(
            data, cover.filename or "", container, params.crf, password
        )
        return Response(
            content=stego_bytes,
            media_type="video/mp4",
            headers=_stego_headers(stats, crf=params.crf, psnr_db=psnr_db,
                                   ber_val=_bit_error_rate(stats),
                                   container_bytes=len(container),
                                   preset_id=params.preset_id.value if params.preset_id else None),
        )

    # ---- IMAGE cover: PNG/BMP -> lossless LSB, else block DCT-QIM ---------
    engine = _detect_image_engine_lenient(data)
    rgb = _decode_image(data)
    if engine == "lsb":
        container = build_container(
            payload_bytes, container_type,
            compression_preset=params.container_tier_id, password=password or None,
            original_filename=fname, mime_type=mime,
            compress=(payload_comp == "DEFLATE"), use_ecc=True,
        )
        png = _encode_lsb(data, container, password, bpc=params.bpc)
        return Response(content=png, media_type="image/png",
                        headers=_lsb_metric_headers(rgb, png, len(container)))

    container = build_container(
        payload_bytes, container_type,
        compression_preset=params.container_tier_id, password=password or None,
        original_filename=fname, mime_type=mime,
        compress=(payload_comp == "DEFLATE"), use_ecc=True,
    )
    jpeg, stats = _encode_jpeg(rgb, container, params.qf, delta=params.delta)
    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers=_image_metric_headers(stats, rgb, jpeg, container_bytes=len(container)),
    )


@router.post(
    "/decode",
    response_model=DecodeResponse,
    responses={400: {"model": ErrorResponse}},
    summary="Extract the payload hidden in a stego image or video",
)
async def stego_decode(
    stego: UploadFile = File(..., description="Stego image or video"),
    password: str = Form("", description="Password used at encode time"),
) -> DecodeResponse:
    data = await stego.read()
    if not data:
        raise StegoError(StegoErrorCode.UPLOAD_EMPTY, "Empty stego upload.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise StegoError(StegoErrorCode.UPLOAD_TOO_LARGE, "Stego file too large.")

    cover_type = _detect_cover_type(stego)

    if cover_type == CoverType.VIDEO:
        # ---- VIDEO stego (I-frame DCT-QIM) -------------------------------
        suffix = os.path.splitext(stego.filename or "")[1] or ".mp4"
        tmp_stego = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(data)
                tmp_stego = tmp.name
            blob = extract_video(tmp_stego, password or None)
        except (VideoEmbedError, VideoProbeError) as exc:
            raise StegoError(StegoErrorCode.VIDEO_PROBE_FAILED, f"Could not read video: {exc}")
        except Exception as exc:  # noqa: BLE001 - never leak a 500 from decode
            raise StegoError(StegoErrorCode.DECODE_RECOVERY_FAILED, f"Could not read video: {exc}")
        finally:
            if tmp_stego and os.path.exists(tmp_stego):
                os.unlink(tmp_stego)
    else:
        # ---- IMAGE stego: PNG/BMP -> lossless LSB, else block DCT-QIM ------
        engine = _detect_image_engine(data)
        try:
            blob = _extract_lsb(data, password) if engine == "lsb" else extract_payload(data)
        except Exception as exc:  # noqa: BLE001 - not a stego image
            raise StegoError(
                StegoErrorCode.DECODE_NO_PAYLOAD,
                f"Could not read a stego payload from this image: {exc}",
            )
        if not blob:
            raise StegoError(
                StegoErrorCode.DECODE_NO_PAYLOAD,
                "No embeddable payload found in this image (no valid DCT-QIM frame).",
            )
        try:
            header, payload = parse_container(blob, password=password or None)
        except ValueError as exc:
            raise StegoError(
                StegoErrorCode.DECODE_RECOVERY_FAILED,
                f"Could not recover the payload: {exc}",
            )

        return _decode_response(header, payload)

    if not blob:
        raise StegoError(
            StegoErrorCode.DECODE_NO_PAYLOAD,
            "No embeddable payload found in this video (no valid DCT-QIM frame).",
        )
    try:
        header, payload = parse_container(blob, password=password or None)
    except ValueError as exc:
        raise StegoError(
            StegoErrorCode.DECODE_RECOVERY_FAILED,
            f"Could not recover the payload: {exc}",
        )

    return _decode_response(header, payload)


# ---------------------------------------------------------------------------
# Dedicated image endpoints (lossless LSB for PNG/BMP, DCT-QIM for JPEG)
# ---------------------------------------------------------------------------

#: Image formats the LSB engine tolerates (lossless).
_LSB_FORMATS = {"PNG", "BMP"}
#: Formats that funnel through the block DCT-QIM engine (lossy).
_DCT_FORMATS = {"JPEG"}


def _detect_image_engine(data: bytes) -> str:
    """Return "lsb" or "dct" for a cover/stego image, or a structured 400."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = (img.format or "").upper()
    except Exception as exc:  # noqa: BLE001 - surface as a clean 400
        raise StegoError(StegoErrorCode.IMAGE_DECODE_FAILED, f"Could not decode image: {exc}")
    if fmt in _LSB_FORMATS:
        return "lsb"
    if fmt in _DCT_FORMATS:
        return "dct"
    raise StegoError(
        StegoErrorCode.IMAGE_FORMAT_UNSUPPORTED,
        f"Unsupported image format '{fmt or 'unknown'}'. Use PNG/BMP "
        "(lossless LSB) or JPEG (DCT-QIM).",
    )


def _detect_image_engine_lenient(data: bytes) -> str:
    """Engine for capacity estimation: 'lsb' for lossless PNG/BMP, else 'dct'.

    Unlike :func:`_detect_image_engine`, this never 400s on formats the
    capacity model can still estimate (e.g. WebP): only PNG/BMP get the
    spatial model, everything else falls through to the JPEG DCT model.
    """
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = (img.format or "").upper()
    except Exception:  # noqa: BLE001 - decode failure already surfaced upstream
        return "dct"
    return "lsb" if fmt in _LSB_FORMATS else "dct"


def _encode_lsb(cover_data: bytes, container: bytes, password: str, bpc: int = 1) -> bytes:
    """Embed a fully built HSTG v2 container into a PNG/BMP cover via LSB.

    Exact fit check against the spatial budget the capacity model advertises
    (``spatial_container_budget`` at the engine's bit depth) BEFORE embedding.
    """
    rgb = _decode_image(cover_data)
    h, w = rgb.shape[:2]
    budget = spatial_container_budget(h, w, bits_per_channel=bpc)
    if len(container) > budget:
        raise StegoError(
            StegoErrorCode.IMAGE_CAPACITY_EXCEEDED,
            f"Payload does not fit this cover: container is {len(container)} B, "
            f"the lossless spatial budget is {budget} B. Use a larger cover or "
            "a smaller/compressed payload.",
        )
    try:
        result = LSBEmbedder(bits_per_channel=bpc).embed(rgb, container, password)
    except ValueError as exc:
        raise StegoError(StegoErrorCode.IMAGE_CAPACITY_EXCEEDED, f"Payload too large for cover: {exc}")
    out = io.BytesIO()
    Image.fromarray(result.stego_media).save(out, format="PNG")
    return out.getvalue()


def _extract_lsb(data: bytes, password: str) -> bytes:
    """Extract an HSTG container from an LSB stego image."""
    try:
        rgb = _decode_image(data)
        return LSBEmbedder().extract(rgb, password)
    except ValueError as exc:
        raise StegoError(StegoErrorCode.DECODE_RECOVERY_FAILED, f"Could not extract container: {exc}")


@router.post(
    "/image/encode",
    responses={400: {"model": ErrorResponse}},
    summary="Embed a payload into an image (PNG/BMP via LSB, JPEG via DCT-QIM)",
)
async def stego_image_encode(
    cover: UploadFile = File(..., description="Cover image (PNG/BMP/JPEG)"),
    payload_type: str = Form("TEXT_MESSAGE", description="TEXT_MESSAGE | TEXT_FILE"),
    # PRIMARY: the single user-facing preset axis (unified presets).
    preset: str = Form(
        "LOSSLESS",
        description="Unified preset: LOSSLESS | CHAT_STANDARD | CHAT_HD (legacy: light | standard | heavy)",
    ),
    # LEGACY: kept for backward compatibility (see _resolve_effective_encode_params)
    carrier_preset: CarrierPreset = Form(
        CarrierPreset.CHAT_STANDARD,
        description="LEGACY: Carrier preset: chat_standard | chat_hd | lossless_high_capacity",
    ),
    payload_compression: Optional[PayloadCompression] = Form(
        None,
        description="LEGACY: Payload compression: NO_COMPRESSION | DEFLATE (explicit choice wins over the preset default)",
    ),
    password: str = Form("", description="Optional encryption password"),
    compress: bool = Form(False, description="LEGACY: Apply DEFLATE (legacy; use payload_compression)"),
    compression_preset: CompressionPreset = Form(
        CompressionPreset.NO_COMPRESSION,
        description="LEGACY: Channel compression preset (NO_COMPRESSION | CHAT_STANDARD | CHAT_HD)",
    ),
    message: str = Form("", description="Payload text (TEXT_MESSAGE)"),
    payload_file: Optional[UploadFile] = File(None, description="Payload file (TEXT_FILE)"),
) -> Response:
    cover_type = _detect_cover_type(cover)
    if cover_type != CoverType.IMAGE:
        raise StegoError(StegoErrorCode.PAYLOAD_COMBO_INVALID, "/image/encode expects an image cover.")
    validated = _validate_combo(cover_type, payload_type)

    data = await cover.read()
    _validate_upload(data, is_video=False)

    # ---- Resolve the single preset axis (unified, with legacy fallbacks) ---
    params = _resolve_effective_encode_params(
        cover_type=cover_type,
        carrier_format=(os.path.splitext(cover.filename or "")[1] or "").lstrip("."),
        payload_type=validated,
        preset_token=preset,
        carrier_preset=carrier_preset,
        payload_compression=payload_compression,
        compress=compress,
    )
    payload_comp = _apply_payload_compression(params, payload_compression, compress)

    engine = _detect_image_engine(data)
    payload_bytes, container_type, fname, mime = await _assemble_payload(
        validated, message, payload_file, None
    )

    if engine == "lsb":
        cover_rgb = _decode_image(data)
        container = build_container(
            payload_bytes,
            container_type,
            compression_preset=params.container_tier_id,
            password=password or None,
            original_filename=fname,
            mime_type=mime,
            compress=(payload_comp == "DEFLATE"), use_ecc=True,
        )
        png = _encode_lsb(data, container, password, bpc=params.bpc)
        return Response(content=png, media_type="image/png",
                        headers=_lsb_metric_headers(cover_rgb, png, len(container)))

    # JPEG -> block DCT-QIM
    rgb = _decode_image(data)
    container = build_container(
        payload_bytes,
        container_type,
        compression_preset=params.container_tier_id,
        password=password or None,
        original_filename=fname,
        mime_type=mime,
        compress=(payload_comp == "DEFLATE"), use_ecc=True,
    )
    jpeg, stats = _encode_jpeg(rgb, container, params.qf, delta=params.delta)
    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers=_image_metric_headers(stats, rgb, jpeg, container_bytes=len(container)),
    )


@router.post(
    "/image/decode",
    response_model=DecodeResponse,
    responses={400: {"model": ErrorResponse}},
    summary="Extract the payload hidden in a stego image",
)
async def stego_image_decode(
    stego: UploadFile = File(..., description="Stego image (PNG/BMP/JPEG)"),
    password: str = Form("", description="Password used at encode time"),
) -> DecodeResponse:
    data = await stego.read()
    if not data:
        raise StegoError(StegoErrorCode.UPLOAD_EMPTY, "Empty stego upload.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise StegoError(StegoErrorCode.UPLOAD_TOO_LARGE, "Stego file too large.")

    engine = _detect_image_engine(data)

    if engine == "lsb":
        blob = _extract_lsb(data, password)
        try:
            header, payload = parse_container(blob, password=password or None)
        except ValueError as exc:
            raise StegoError(StegoErrorCode.DECODE_RECOVERY_FAILED, f"Could not recover the payload: {exc}")
        return _decode_response(header, payload)

    try:
        blob = extract_payload(data)
    except Exception as exc:  # noqa: BLE001 - not a stego frame
        raise StegoError(
            StegoErrorCode.DECODE_NO_PAYLOAD,
            f"Could not read a stego payload from this image: {exc}",
        )
    if not blob:
        raise StegoError(
            StegoErrorCode.DECODE_NO_PAYLOAD,
            "No embeddable payload found in this image (no valid DCT-QIM frame).",
        )
    try:
        header, payload = parse_container(blob, password=password or None)
    except ValueError as exc:
        raise StegoError(
            StegoErrorCode.DECODE_RECOVERY_FAILED,
            f"Could not recover the payload: {exc}",
        )
    return _decode_response(header, payload)


# ---------------------------------------------------------------------------
# Dedicated video endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/video/encode",
    responses={400: {"model": ErrorResponse}},
    summary="Embed a payload into a video (I-frame DCT-QIM + H.264 CRF re-encode)",
)
async def stego_video_encode(
    cover: UploadFile = File(..., description="Cover video"),
    payload_type: str = Form("TEXT_MESSAGE", description="TEXT_MESSAGE | TEXT_FILE | IMAGE"),
    # PRIMARY: the single user-facing preset axis (unified presets).
    preset: str = Form(
        "LOSSLESS",
        description="Unified preset: LOSSLESS | CHAT_STANDARD | CHAT_HD (legacy: light | standard | heavy)",
    ),
    # LEGACY: kept for backward compatibility (see _resolve_effective_encode_params)
    carrier_preset: CarrierPreset = Form(
        CarrierPreset.CHAT_STANDARD,
        description="LEGACY: Carrier preset: chat_standard | chat_hd | lossless_high_capacity",
    ),
    payload_compression: Optional[PayloadCompression] = Form(
        None,
        description="LEGACY: Payload compression: NO_COMPRESSION | DEFLATE (explicit choice wins over the preset default)",
    ),
    password: str = Form("", description="Optional encryption password"),
    compress: bool = Form(False, description="LEGACY: Apply DEFLATE (legacy; use payload_compression)"),
    compression_preset: CompressionPreset = Form(
        CompressionPreset.NO_COMPRESSION,
        description="LEGACY: Channel compression preset (NO_COMPRESSION | CHAT_STANDARD | CHAT_HD)",
    ),
    message: str = Form("", description="Payload text (TEXT_MESSAGE)"),
    payload_file: Optional[UploadFile] = File(None, description="Payload file (TEXT_FILE)"),
    payload_image: Optional[UploadFile] = File(None, description="Payload image (IMAGE)"),
) -> Response:
    cover_type = _detect_cover_type(cover)
    if cover_type != CoverType.VIDEO:
        raise StegoError(StegoErrorCode.PAYLOAD_COMBO_INVALID, "/video/encode expects a video cover.")
    validated = _validate_combo(cover_type, payload_type)

    data = await cover.read()
    _validate_upload(data, is_video=True)

    # ---- Resolve the single preset axis (unified, with legacy fallbacks) ---
    params = _resolve_effective_encode_params(
        cover_type=cover_type,
        carrier_format=(os.path.splitext(cover.filename or "")[1] or "").lstrip("."),
        payload_type=validated,
        preset_token=preset,
        carrier_preset=carrier_preset,
        payload_compression=payload_compression,
        compress=compress,
    )
    payload_comp = _apply_payload_compression(params, payload_compression, compress)

    payload_bytes, container_type, fname, mime = await _assemble_payload(
        validated, message, payload_file, payload_image
    )
    container = build_container(
        payload_bytes,
        container_type,
        compression_preset=params.container_tier_id,
        password=password or None,
        original_filename=fname,
        mime_type=mime,
        compress=(payload_comp == "DEFLATE"), use_ecc=True,
    )
    stego_bytes, stats, psnr_db = _encode_video(
        data, cover.filename or "", container, params.crf, password
    )
    return Response(
        content=stego_bytes,
        media_type="video/mp4",
        headers=_stego_headers(stats, crf=params.crf, psnr_db=psnr_db,
                               ber_val=_bit_error_rate(stats),
                               container_bytes=len(container),
                               preset_id=params.preset_id.value if params.preset_id else None),
    )


@router.post(
    "/video/decode",
    response_model=DecodeResponse,
    responses={400: {"model": ErrorResponse}},
    summary="Extract the payload hidden in a stego video",
)
async def stego_video_decode(
    stego: UploadFile = File(..., description="Stego video"),
    password: str = Form("", description="Password used at encode time"),
) -> DecodeResponse:
    data = await stego.read()
    if not data:
        raise StegoError(StegoErrorCode.UPLOAD_EMPTY, "Empty stego upload.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise StegoError(StegoErrorCode.UPLOAD_TOO_LARGE, "Stego file too large.")

    cover_type = _detect_cover_type(stego)
    if cover_type != CoverType.VIDEO:
        raise StegoError(StegoErrorCode.PAYLOAD_COMBO_INVALID, "/video/decode expects a video stego file.")

    suffix = os.path.splitext(stego.filename or "")[1] or ".mp4"
    tmp_stego = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_stego = tmp.name
        blob = extract_video(tmp_stego, password or None)
    except (VideoEmbedError, VideoProbeError) as exc:
        raise StegoError(StegoErrorCode.VIDEO_PROBE_FAILED, f"Could not read video: {exc}")
    except Exception as exc:  # noqa: BLE001 - never leak a 500 from video decode
        raise StegoError(StegoErrorCode.DECODE_RECOVERY_FAILED, f"Could not read video: {exc}")
    finally:
        if tmp_stego and os.path.exists(tmp_stego):
            os.unlink(tmp_stego)

    if not blob:
        raise StegoError(
            StegoErrorCode.DECODE_NO_PAYLOAD,
            "No embeddable payload found in this video (no valid DCT-QIM frame).",
        )
    try:
        header, payload = parse_container(blob, password=password or None)
    except ValueError as exc:
        raise StegoError(
            StegoErrorCode.DECODE_RECOVERY_FAILED,
            f"Could not recover the payload: {exc}",
        )
    return _decode_response(header, payload)
