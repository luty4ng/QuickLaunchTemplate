"""Liveness/readiness endpoint.

`/healthz` answers "the process is up"; `/api/health` answers "the process is
up AND it can reach its database", which is what CD gates on. A container that
is running but cannot reach Postgres must not be reported as healthy.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app import __version__
from app.db.session import SessionLocal
from app.schemas import HealthResponse

router = APIRouter(tags=["ops"])


@router.get("/healthz", include_in_schema=False)
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/health", response_model=HealthResponse, responses={503: {"model": HealthResponse}})
async def health(response: Response) -> HealthResponse:
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - any failure means "not ready"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(status="degraded", database="down", version=__version__)
    return HealthResponse(status="ok", database="up", version=__version__)
