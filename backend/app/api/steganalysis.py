"""
Dedicated steganalysis endpoints.

POST /api/steganalysis/sequential-ws — sequential Weighted Stego (Ker) with
prefix/window scanning. The combined four-detector Analyze UI still lives at
POST /api/stego/analyze.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, File, Form, UploadFile

from app.api.stego import _decode_image, _detect_cover_type, _validate_upload
from app.core.errors import StegoError
from app.models.stego import (
    CoverType,
    ErrorResponse,
    SequentialWsResult,
    StegoErrorCode,
)
from modules.steganalysis.sequential_ws import SequentialWS

router = APIRouter(prefix="/steganalysis", tags=["steganalysis"])


def sequential_ws_to_model(result) -> SequentialWsResult:
    """Map the detector dataclass onto the public Pydantic schema."""
    return SequentialWsResult(**result.as_api_dict())


@router.post(
    "/sequential-ws",
    response_model=SequentialWsResult,
    responses={400: {"model": ErrorResponse}},
    summary="Sequential Weighted Stego scan for raster-prefix LSB replacement",
)
async def sequential_ws_analyze(
    cover: UploadFile = File(..., description="Image to analyse (PNG/JPEG/BMP)"),
    candidate_min: Optional[int] = Form(None),
    candidate_max: Optional[int] = Form(None),
    n_candidates: Optional[int] = Form(None),
    mode: str = Form("prefix"),
) -> SequentialWsResult:
    """Ker sequential WS on decoded RGB, per channel, with BH-corrected prefixes.

    A suspicious decision is statistically suspicious for sequential LSB
    replacement. It does not prove that hidden data exists.
    """
    cover_type = _detect_cover_type(cover)
    if cover_type != CoverType.IMAGE:
        raise StegoError(
            StegoErrorCode.PAYLOAD_COMBO_INVALID,
            "/steganalysis/sequential-ws expects an image (PNG/JPEG/BMP).",
        )
    data = await cover.read()
    _validate_upload(data, is_video=False)
    rgb = _decode_image(data)

    mode_norm = (mode or "prefix").strip().lower()
    if mode_norm not in ("prefix", "window"):
        raise StegoError(
            StegoErrorCode.PRESET_INVALID,
            "mode must be 'prefix' or 'window'.",
        )
    if candidate_min is not None and candidate_min < 1:
        raise StegoError(StegoErrorCode.PRESET_INVALID, "candidate_min must be >= 1.")
    if candidate_max is not None and candidate_max < 1:
        raise StegoError(StegoErrorCode.PRESET_INVALID, "candidate_max must be >= 1.")
    if n_candidates is not None and n_candidates < 1:
        raise StegoError(StegoErrorCode.PRESET_INVALID, "n_candidates must be >= 1.")

    kwargs = {
        "mode": mode_norm,
        "candidate_min": 256 if candidate_min is None else int(candidate_min),
        "candidate_max": candidate_max,
        "n_candidates": n_candidates,
    }
    result = SequentialWS.detect(rgb, **kwargs)
    return sequential_ws_to_model(result)
