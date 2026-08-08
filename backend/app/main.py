"""
Harpocrates backend FastAPI application.

Builds the REST layer that was scaffolded-but-empty in ``backend/app/`` (audit
§2/§7.4). Mounts the stego router so the frontend can call the real
preset-aware capacity calculator instead of its client-side mock.

Run locally:
    uvicorn app.main:app --reload --port 8000
(from the ``backend/`` directory, with the venv active)
"""
from __future__ import annotations

import os
import sys

# Ensure ``modules`` (backend/modules) is importable when launched from various
# working directories, mirroring the pattern used in backend/tests/*.
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from app.api.stego import router as stego_router  # noqa: E402

app = FastAPI(
    title="Harpocrates API",
    version="0.1.0",
    description="Steganography capacity + container services.",
)

# CORS: the Vite dev server (and the existing Express api-server) call this from
# a different origin. Open in dev to match the existing api-server's cors()
# posture (audit §4); lock down via env allowlist for real deployments.
_allowed = os.environ.get("CORS_ALLOW_ORIGINS", "*")
_origins = ["*"] if _allowed.strip() == "*" else [o.strip() for o in _allowed.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    # Custom encode metrics live in response headers; browsers hide non-simple
    # response headers from cross-origin JS unless they are explicitly exposed.
    # The Encode result panel reads these to show per-request PSNR/SSIM/BER,
    # the container size, and the CRF.
    expose_headers=[
        "X-Stego-PSNR", "X-Stego-SSIM", "X-Stego-BER",
        "X-Stego-Container-Bytes", "X-Stego-CRF",
        "X-Stego-Iterations", "X-Stego-Residual-Bits",
        "X-Stego-Blocks-Eligible", "X-Stego-Blocks-Used",
    ],
)


@app.get("/api/healthz", tags=["health"])
async def healthz() -> dict:
    """Health check (mirrors the Express api-server's /api/healthz)."""
    return {"status": "ok"}


# All stego routes live under /api (matches the frontend api-client base URL).
app.include_router(stego_router, prefix="/api")
