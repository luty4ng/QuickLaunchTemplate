"""Serve the desktop update feed and the installers it points at.

Installed clients need two things from this project's own domain:

* `/updates/latest.yml` - the feed, which says which version exists and where the
  installer is;
* the installer itself, plus its `.blockmap` next to it, because electron-updater
  derives the blockmap URL by appending `.blockmap` to the installer URL and uses
  HTTP range requests to fetch only the blocks that changed. Serving the installer
  from anywhere else (a GitHub release asset, say) would make that request a 404
  and silently turn every update into a full 107 MB download.

The files themselves are written by the release pipeline into `~/<slug>/updates/`,
which `deploy/compose.server.yaml` mounts read-only at `/app/updates`. Nothing in
the application writes there.

    GET /updates/latest.yml                                  the feed (404 if none)
    GET /updates/QuickLaunch-Setup-1.2.4-x64.exe             the installer
    GET /updates/QuickLaunch-Setup-1.2.4-x64.exe.blockmap    its block map
"""

from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.deps import api_error

# Not in the OpenAPI docs: it is not part of the public API surface, it is what a
# desktop client polls and downloads.
router = APIRouter(prefix="/updates", tags=["updates"], include_in_schema=False)


@router.get("/latest.yml")
async def update_feed() -> FileResponse:
    """The desktop update feed, as published by the release pipeline."""
    path = get_settings().updates_file
    if not path.is_file():
        raise api_error(
            "no_update_feed",
            "No desktop update feed has been published yet.",
            status.HTTP_404_NOT_FOUND,
        )
    return FileResponse(path, media_type="text/yaml")


def mount_installers(app) -> None:
    """Serve the rest of the updates directory: installers and block maps.

    Mounted *after* the feed route so `/updates/latest.yml` keeps its JSON 404
    (a client looking for the feed should not get a bare static-file 404 - the two
    are told apart in the logs). `check_dir=False` because the directory is
    created by the first deployment; until then every request here is a 404, which
    is the honest answer.
    """
    directory = get_settings().updates_file.parent
    app.mount(
        "/updates",
        StaticFiles(directory=directory, check_dir=False),
        name="updates",
    )
