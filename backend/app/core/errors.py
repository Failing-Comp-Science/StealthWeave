"""
Structured stego API errors (Harpocrates).

FastAPI's ``HTTPException`` only carries a ``detail`` string. The task requires
a stable, machine-readable error ``code`` alongside the human message so the
frontend can branch (e.g. distinguish a user-recoverable "payload too large"
from an engine/environment failure). ``StegoError`` carries both and is
rendered by :func:`stego_error_handler` into ``{"detail": ..., "code": ...}``,
matching the :class:`app.models.stego.ErrorResponse` schema.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from app.models.stego import StegoErrorCode


class StegoError(Exception):
    """An API error with a stable code and an HTTP status.

    Prefer this over ``HTTPException`` for every stego endpoint failure so the
    response always includes a ``code`` the UI can act on.
    """

    def __init__(
        self,
        code: StegoErrorCode,
        detail: str,
        status_code: int = 400,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status_code = status_code


async def stego_error_handler(_request: Request, exc: StegoError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "code": exc.code.value},
    )
