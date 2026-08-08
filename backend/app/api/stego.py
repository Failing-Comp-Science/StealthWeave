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
from modules.capacity import image_capacity, video_capacity
from modules.capacity.dct_embedder import CapacityError, encode_jpeg, extract_payload
from modules.capacity.presets import IMAGE_PRESETS, VIDEO_PRESETS
from modules.capacity.video_capacity import VideoProbeError
from modules.image_stego.lsb import LSBEmbedder
from modules.metrics import psnr, ssim
from modules.video_stego import VideoEmbedError, embed_video, extract_video
from modules.video_stego._codec import video_psnr
from modules.container import (
    CompressionPreset as ContainerCompressionPreset,
    CompressionPresetId,
    PayloadType as ContainerPayloadType,
    build_container,
    parse_container,
)

from app.models.stego import (
    ALLOWED_PAYLOADS,
    KNOWN_PAYLOAD_TOKENS,
    CapacityResponse,
    CompressionPreset,
    CoverType,
    DecodeResponse,
    ErrorResponse,
    PayloadType,
    PresetCapacity,
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


def _detect_cover_type(upload: UploadFile) -> CoverType:
    content_type = (upload.content_type or "").lower()
    name = (upload.filename or "").lower()
    if content_type.startswith("image/") or name.endswith(_IMAGE_EXTS):
        return CoverType.IMAGE
    if content_type.startswith("video/") or name.endswith(_VIDEO_EXTS):
        return CoverType.VIDEO
    raise HTTPException(
        status_code=400,
        detail=(
            "Unsupported cover type. Upload an image (PNG/JPG/WebP/BMP) or a "
            "video (MP4/WebM/MOV)."
        ),
    )


def _validate_combo(cover_type: CoverType, raw_payload_type: str) -> PayloadType:
    token = (raw_payload_type or "").strip().upper()
    if token not in KNOWN_PAYLOAD_TOKENS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown payload_type '{raw_payload_type}'. Expected one of "
                "TEXT_MESSAGE, TEXT_FILE, IMAGE."
            ),
        )
    allowed = ALLOWED_PAYLOADS[cover_type]
    allowed_names = [p.value for p in allowed]
    if token not in allowed_names:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{token} payload is not allowed in a {cover_type.value} cover. "
                f"Allowed payload types for this cover: {', '.join(allowed_names)}."
            ),
        )
    return PayloadType(token)


def _decode_image(data: bytes) -> np.ndarray:
    try:
        with Image.open(io.BytesIO(data)) as img:
            return np.asarray(img.convert("RGB"))
    except Exception as exc:  # noqa: BLE001 - surface as a clean 400
        raise HTTPException(status_code=400, detail=f"Could not decode image: {exc}")


def _stego_headers(
    stats,
    crf: Optional[int] = None,
    psnr_db: Optional[float] = None,
    ssim_val: Optional[float] = None,
    ber_val: Optional[float] = None,
    container_bytes: Optional[int] = None,
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
    except HTTPException:
        return None


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
    compression_preset: CompressionPreset = Query(
        CompressionPreset.NO_COMPRESSION,
        description="Channel compression preset (NO_COMPRESSION | CHAT_STANDARD | CHAT_HD)",
    ),
    cover: UploadFile = File(..., description="Cover image or video"),
) -> CapacityResponse:
    cover_type = _detect_cover_type(cover)
    validated_payload = _validate_combo(cover_type, payload_type)

    data = await cover.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty cover upload.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Cover file too large.")

    container_preset = _resolve_container_preset(compression_preset, compress=False)

    if cover_type == CoverType.IMAGE:
        rgb = _decode_image(data)
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
            raise HTTPException(status_code=400, detail=f"Could not read video: {exc}")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    return CapacityResponse(
        cover_type=cover_type,
        payload_type=validated_payload,
        compression_preset=compression_preset,
        allowed_payload_types=ALLOWED_PAYLOADS[cover_type],
        container_version=HEADER_VERSION_V2,
        presets=[PresetCapacity(**p) for p in presets],
    )


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
        raise HTTPException(
            status_code=400,
            detail=f"Unknown preset '{raw}'. Expected light | standard | heavy or a quality factor 1-100.",
        )
    if not 1 <= qf <= 100:
        raise HTTPException(status_code=400, detail="Quality factor must be in 1..100.")
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
        raise HTTPException(
            status_code=400,
            detail=f"Unknown preset '{raw}'. Expected light | standard | heavy or a CRF 18-32.",
        )
    if not 18 <= crf <= 32:
        raise HTTPException(status_code=400, detail="CRF must be in 18..32.")
    return crf


async def _assemble_payload(
    validated: PayloadType,
    message: str,
    payload_file: Optional[UploadFile],
    payload_image: Optional[UploadFile],
) -> tuple[bytes, ContainerPayloadType, str, str]:
    """Read + validate the payload fields; return (bytes, container type, fname, mime)."""
    if validated == PayloadType.TEXT_MESSAGE:
        if not message:
            raise HTTPException(status_code=400, detail="TEXT_MESSAGE payload requires a 'message' field.")
        return message.encode("utf-8"), ContainerPayloadType.TEXT_MESSAGE, "", ""
    if validated == PayloadType.TEXT_FILE:
        if payload_file is None or not (payload_file.filename or "").strip():
            raise HTTPException(status_code=400, detail="TEXT_FILE payload requires a 'payload_file' upload.")
        payload_bytes = await payload_file.read()
        if not payload_bytes:
            raise HTTPException(status_code=400, detail="Empty payload file.")
        fname = os.path.basename(payload_file.filename or "payload")
        mime = payload_file.content_type or "application/octet-stream"
        return payload_bytes, ContainerPayloadType.TEXT_FILE, fname, mime
    # IMAGE payload (video covers only; the _validate_combo matrix rejects it elsewhere)
    if payload_image is None or not (payload_image.filename or "").strip():
        raise HTTPException(status_code=400, detail="IMAGE payload requires a 'payload_image' upload.")
    payload_bytes = await payload_image.read()
    if not payload_bytes:
        raise HTTPException(status_code=400, detail="Empty payload image.")
    fname = os.path.basename(payload_image.filename or "payload.png")
    mime = payload_image.content_type or "image/png"
    return payload_bytes, ContainerPayloadType.IMAGE, fname, mime


@router.post(
    "/encode",
    responses={400: {"model": ErrorResponse}},
    summary="Embed a payload into a cover image or video (DCT-QIM)",
)
async def stego_encode(
    cover: UploadFile = File(..., description="Cover image or video"),
    payload_type: str = Form("TEXT_MESSAGE", description="TEXT_MESSAGE | TEXT_FILE | IMAGE"),
    preset: str = Form("light", description="light | standard | heavy, or a JPEG quality factor / CRF"),
    password: str = Form("", description="Optional encryption password"),
    compress: bool = Form(False, description="Apply DEFLATE in the container (legacy; prefer compression_preset)"),
    compression_preset: CompressionPreset = Form(
        CompressionPreset.NO_COMPRESSION,
        description="Channel compression preset (NO_COMPRESSION | CHAT_STANDARD | CHAT_HD)",
    ),
    message: str = Form("", description="Payload text (TEXT_MESSAGE)"),
    payload_file: Optional[UploadFile] = File(None, description="Payload file (TEXT_FILE)"),
    payload_image: Optional[UploadFile] = File(None, description="Payload image (IMAGE, video covers only)"),
) -> Response:
    cover_type = _detect_cover_type(cover)
    validated = _validate_combo(cover_type, payload_type)

    data = await cover.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty cover upload.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Cover file too large.")

    # ---- Assemble the HSTG v2 container payload ---------------------------
    payload_bytes, container_type, fname, mime = await _assemble_payload(
        validated, message, payload_file, payload_image
    )
    container_preset = _resolve_container_preset(compression_preset, compress)

    if cover_type == CoverType.VIDEO:
        # ---- VIDEO cover (I-frame DCT-QIM + H.264 CRF re-encode) ----------
        crf = _resolve_video_preset(preset)
        carrier_preset = (
            CompressionPresetId.LIGHT if crf <= 20 else
            CompressionPresetId.STANDARD if crf <= 25 else CompressionPresetId.HEAVY
        )
        suffix = os.path.splitext(cover.filename or "")[1] or ".mp4"
        tmp_cover = None
        tmp_stego = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(data)
                tmp_cover = tmp.name
            container = build_container(
                payload_bytes,
                container_type,
                compression_preset=carrier_preset,
                password=password or None,
                original_filename=fname,
                mime_type=mime,
                compress=container_preset,
                use_ecc=True,
            )
            stego_bytes, stats = embed_video(tmp_cover, container, crf, password or None)

            # Per-encode video PSNR (bounded frame sample to keep latency sane).
            psnr_db = None
            if stego_bytes:
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(stego_bytes)
                    tmp_stego = tmp.name
                try:
                    psnr_db = video_psnr(tmp_cover, tmp_stego, max_frames=VIDEO_PSNR_MAX_FRAMES)
                except ValueError:
                    psnr_db = None
        except VideoEmbedError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        finally:
            if tmp_cover and os.path.exists(tmp_cover):
                os.unlink(tmp_cover)
            if tmp_stego and os.path.exists(tmp_stego):
                os.unlink(tmp_stego)
        return Response(
            content=stego_bytes,
            media_type="video/mp4",
            headers=_stego_headers(stats, crf=crf, psnr_db=psnr_db,
                                   ber_val=_bit_error_rate(stats),
                                   container_bytes=len(container)),
        )

    # ---- IMAGE cover (block DCT-QIM + JPEG quality factor) ---------------
    qf = _resolve_preset(preset)
    rgb = _decode_image(data)

    try:
        carrier_preset = CompressionPresetId.LIGHT if qf >= 90 else (
            CompressionPresetId.STANDARD if qf >= 80 else CompressionPresetId.HEAVY
        )
        container = build_container(
            payload_bytes,
            container_type,
            compression_preset=carrier_preset,
            password=password or None,
            original_filename=fname,
            mime_type=mime,
            compress=container_preset,
            use_ecc=True,
        )
        # Delta trade-off: 2.0 (more margin, robust) works at high QF where
        # re-quantization drift is small; 1.0 (finer levels) survives the
        # clipped carriers that dominate low QF. The frame records which one
        # was used, so the decoder needs no extra parameter.
        delta = 2.0 if qf >= 90 else 1.0
        jpeg, stats = encode_jpeg(rgb, container, qf, delta=delta)
    except CapacityError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

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
        raise HTTPException(status_code=400, detail="Empty stego upload.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Stego file too large.")

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
        except VideoEmbedError as exc:
            raise HTTPException(status_code=400, detail=f"Could not read video: {exc}")
        finally:
            if tmp_stego and os.path.exists(tmp_stego):
                os.unlink(tmp_stego)
    else:
        # ---- IMAGE stego (block DCT-QIM) ---------------------------------
        try:
            blob = extract_payload(data)
        except Exception as exc:  # noqa: BLE001 - not a JPEG / not a stego frame
            raise HTTPException(
                status_code=400,
                detail=f"Could not read a stego payload from this image: {exc}",
            )
        if not blob:
            raise HTTPException(
                status_code=400,
                detail="No embeddable payload found in this image (no valid DCT-QIM frame).",
            )
        try:
            header, payload = parse_container(blob, password=password or None)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Could not recover the payload: {exc}",
            )

        return _decode_response(header, payload)

    if not blob:
        raise HTTPException(
            status_code=400,
            detail="No embeddable payload found in this video (no valid DCT-QIM frame).",
        )
    try:
        header, payload = parse_container(blob, password=password or None)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Could not recover the payload: {exc}",
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
    """Return "lsb" or "dct" for a cover/stego image, or 400."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = (img.format or "").upper()
    except Exception as exc:  # noqa: BLE001 - surface as a clean 400
        raise HTTPException(status_code=400, detail=f"Could not decode image: {exc}")
    if fmt in _LSB_FORMATS:
        return "lsb"
    if fmt in _DCT_FORMATS:
        return "dct"
    raise HTTPException(
        status_code=400,
        detail=(
            f"Unsupported image format '{fmt or 'unknown'}'. Use PNG/BMP "
            "(lossless LSB) or JPEG (DCT-QIM)."
        ),
    )


def _embed_lsb(cover_data: bytes, container: bytes, password: str) -> bytes:
    """Embed a fully built HSTG v2 container into a PNG/BMP cover via LSB."""
    rgb = _decode_image(cover_data)
    try:
        result = LSBEmbedder(bits_per_channel=1).embed(rgb, container, password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Payload too large for cover: {exc}")
    out = io.BytesIO()
    Image.fromarray(result.stego_media).save(out, format="PNG")
    return out.getvalue()


def _extract_lsb(data: bytes, password: str) -> bytes:
    """Extract an HSTG container from an LSB stego image."""
    try:
        rgb = _decode_image(data)
        return LSBEmbedder().extract(rgb, password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Could not extract container: {exc}")


@router.post(
    "/image/encode",
    responses={400: {"model": ErrorResponse}},
    summary="Embed a payload into an image (PNG/BMP via LSB, JPEG via DCT-QIM)",
)
async def stego_image_encode(
    cover: UploadFile = File(..., description="Cover image (PNG/BMP/JPEG)"),
    payload_type: str = Form("TEXT_MESSAGE", description="TEXT_MESSAGE | TEXT_FILE"),
    preset: str = Form("light", description="light | standard | heavy, or a JPEG quality factor"),
    password: str = Form("", description="Optional encryption password"),
    compress: bool = Form(False, description="Apply DEFLATE in the container (legacy; prefer compression_preset)"),
    compression_preset: CompressionPreset = Form(
        CompressionPreset.NO_COMPRESSION,
        description="Channel compression preset (NO_COMPRESSION | CHAT_STANDARD | CHAT_HD)",
    ),
    message: str = Form("", description="Payload text (TEXT_MESSAGE)"),
    payload_file: Optional[UploadFile] = File(None, description="Payload file (TEXT_FILE)"),
) -> Response:
    cover_type = _detect_cover_type(cover)
    if cover_type != CoverType.IMAGE:
        raise HTTPException(status_code=400, detail="/image/encode expects an image cover.")
    validated = _validate_combo(cover_type, payload_type)

    data = await cover.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty cover upload.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Cover file too large.")

    engine = _detect_image_engine(data)
    payload_bytes, container_type, fname, mime = await _assemble_payload(
        validated, message, payload_file, None
    )
    container_preset = _resolve_container_preset(compression_preset, compress)

    if engine == "lsb":
        cover_rgb = _decode_image(data)
        container = build_container(
            payload_bytes,
            container_type,
            compression_preset=CompressionPresetId.LIGHT,
            password=password or None,
            original_filename=fname,
            mime_type=mime,
            compress=container_preset,
            use_ecc=True,
        )
        png = _embed_lsb(data, container, password)
        stego_rgb = _decode_image_safe(png)
        psnr_db = ssim_val = None
        if stego_rgb is not None and cover_rgb.shape == stego_rgb.shape:
            psnr_db = psnr(cover_rgb, stego_rgb)
            ssim_val = ssim(cover_rgb, stego_rgb)
        # LSB is lossless — no residual channel errors by construction.
        headers = {
            "X-Stego-PSNR": f"{psnr_db:.2f}" if psnr_db is not None else "inf",
            "X-Stego-SSIM": f"{ssim_val:.4f}" if ssim_val is not None else "1.0",
            "X-Stego-BER": "0.0",
            "X-Stego-Container-Bytes": str(len(container)),
        }
        return Response(content=png, media_type="image/png", headers=headers)

    # JPEG -> block DCT-QIM
    qf = _resolve_preset(preset)
    rgb = _decode_image(data)
    carrier_preset = CompressionPresetId.LIGHT if qf >= 90 else (
        CompressionPresetId.STANDARD if qf >= 80 else CompressionPresetId.HEAVY
    )
    container = build_container(
        payload_bytes,
        container_type,
        compression_preset=carrier_preset,
        password=password or None,
        original_filename=fname,
        mime_type=mime,
        compress=container_preset,
        use_ecc=True,
    )
    try:
        delta = 2.0 if qf >= 90 else 1.0
        jpeg, stats = encode_jpeg(rgb, container, qf, delta=delta)
    except CapacityError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
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
        raise HTTPException(status_code=400, detail="Empty stego upload.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Stego file too large.")

    engine = _detect_image_engine(data)

    if engine == "lsb":
        blob = _extract_lsb(data, password)
        try:
            header, payload = parse_container(blob, password=password or None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Could not recover the payload: {exc}")
        return _decode_response(header, payload)

    try:
        blob = extract_payload(data)
    except Exception as exc:  # noqa: BLE001 - not a stego frame
        raise HTTPException(
            status_code=400,
            detail=f"Could not read a stego payload from this image: {exc}",
        )
    if not blob:
        raise HTTPException(
            status_code=400,
            detail="No embeddable payload found in this image (no valid DCT-QIM frame).",
        )
    try:
        header, payload = parse_container(blob, password=password or None)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Could not recover the payload: {exc}",
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
    preset: str = Form("standard", description="light | standard | heavy, or a CRF 18-32"),
    password: str = Form("", description="Optional encryption password"),
    compress: bool = Form(False, description="Apply DEFLATE in the container (legacy; prefer compression_preset)"),
    compression_preset: CompressionPreset = Form(
        CompressionPreset.NO_COMPRESSION,
        description="Channel compression preset (NO_COMPRESSION | CHAT_STANDARD | CHAT_HD)",
    ),
    message: str = Form("", description="Payload text (TEXT_MESSAGE)"),
    payload_file: Optional[UploadFile] = File(None, description="Payload file (TEXT_FILE)"),
    payload_image: Optional[UploadFile] = File(None, description="Payload image (IMAGE)"),
) -> Response:
    cover_type = _detect_cover_type(cover)
    if cover_type != CoverType.VIDEO:
        raise HTTPException(status_code=400, detail="/video/encode expects a video cover.")
    validated = _validate_combo(cover_type, payload_type)

    data = await cover.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty cover upload.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Cover file too large.")

    payload_bytes, container_type, fname, mime = await _assemble_payload(
        validated, message, payload_file, payload_image
    )
    container_preset = _resolve_container_preset(compression_preset, compress)

    crf = _resolve_video_preset(preset)
    carrier_preset = (
        CompressionPresetId.LIGHT if crf <= 20 else
        CompressionPresetId.STANDARD if crf <= 25 else CompressionPresetId.HEAVY
    )
    suffix = os.path.splitext(cover.filename or "")[1] or ".mp4"
    tmp_cover = None
    tmp_stego = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_cover = tmp.name
        container = build_container(
            payload_bytes,
            container_type,
            compression_preset=carrier_preset,
            password=password or None,
            original_filename=fname,
            mime_type=mime,
            compress=container_preset,
            use_ecc=True,
        )
        stego_bytes, stats = embed_video(tmp_cover, container, crf, password or None)

        # Per-encode video PSNR (bounded frame sample to keep latency sane).
        psnr_db = None
        if stego_bytes:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(stego_bytes)
                tmp_stego = tmp.name
            try:
                psnr_db = video_psnr(tmp_cover, tmp_stego, max_frames=VIDEO_PSNR_MAX_FRAMES)
            except ValueError:
                psnr_db = None
    except VideoEmbedError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        if tmp_cover and os.path.exists(tmp_cover):
            os.unlink(tmp_cover)
        if tmp_stego and os.path.exists(tmp_stego):
            os.unlink(tmp_stego)
    return Response(
        content=stego_bytes,
        media_type="video/mp4",
        headers=_stego_headers(stats, crf=crf, psnr_db=psnr_db,
                               ber_val=_bit_error_rate(stats),
                               container_bytes=len(container)),
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
        raise HTTPException(status_code=400, detail="Empty stego upload.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Stego file too large.")

    cover_type = _detect_cover_type(stego)
    if cover_type != CoverType.VIDEO:
        raise HTTPException(status_code=400, detail="/video/decode expects a video stego file.")

    suffix = os.path.splitext(stego.filename or "")[1] or ".mp4"
    tmp_stego = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_stego = tmp.name
        blob = extract_video(tmp_stego, password or None)
    except VideoEmbedError as exc:
        raise HTTPException(status_code=400, detail=f"Could not read video: {exc}")
    finally:
        if tmp_stego and os.path.exists(tmp_stego):
            os.unlink(tmp_stego)

    if not blob:
        raise HTTPException(
            status_code=400,
            detail="No embeddable payload found in this video (no valid DCT-QIM frame).",
        )
    try:
        header, payload = parse_container(blob, password=password or None)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Could not recover the payload: {exc}",
        )
    return _decode_response(header, payload)
