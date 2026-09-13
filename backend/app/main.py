"""FastAPI application factory.

One process serves both the JSON API (`/api/*`) and the compiled web client, so
a deployment is a single image, a single port and a single health check.
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.billing import router as billing
from app.config import REPO_ROOT, get_settings
from app.deps import install_error_handler
from app.routers import auth, health, todos, updates

log = logging.getLogger("quicklaunch")


def run_migrations() -> None:
    """Apply alembic migrations. Idempotent, so it is safe on every boot."""
    settings = get_settings()
    if not settings.auto_migrate:
        return
    log.info("applying database migrations")
    subprocess.run(
        ["alembic", "-c", str(REPO_ROOT / "backend" / "alembic.ini"), "upgrade", "head"],
        check=True,
        # Pass the resolved settings through, otherwise alembic would read a
        # different DATABASE_URL than the app it is migrating for.
        env={**os.environ, "DATABASE_URL": settings.database_url},
    )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    get_settings().prepare_local_dirs()
    run_migrations()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    if settings.cors_origin_list:
        # Only needed when the web client is served from another origin
        # (e.g. the desktop shell during local development).
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:  # pragma: no cover
        log.exception("unhandled error: %s", exc)
        return JSONResponse(
            status_code=500, content={"error": {"code": "internal", "message": "Unexpected server error."}}
        )

    install_error_handler(app)
    app.include_router(health.router)
    app.include_router(auth.router, prefix="/api")
    app.include_router(todos.router, prefix="/api")
    app.include_router(billing.router, prefix="/api")
    # Registered before the SPA catch-all below, which would otherwise answer
    # /updates/latest.yml with index.html.
    app.include_router(updates.router)
    _mount_web_client(app, settings.web_dist)
    return app


def _mount_web_client(app: FastAPI, dist: Path | None) -> None:
    if not dist or not dist.is_dir():
        log.warning("web client not found at %s - serving API only", dist)
        return
    root = dist.resolve()
    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        """Serve real files, and index.html for every other path (SPA routing)."""
        if path:
            candidate = (root / path).resolve()
            if candidate.is_file() and candidate.is_relative_to(root):
                return FileResponse(candidate)
        return FileResponse(root / "index.html")


app = create_app()
