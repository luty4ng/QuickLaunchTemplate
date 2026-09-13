"""Serve the desktop update feed, the block maps, and redirect the installer.

Installed clients need three things from this project's own domain:

* `/updates/latest.yml` - the feed: which version exists, where the installer is,
  and the checksum to verify it with;
* `/updates/<installer>.exe.blockmap` - the block map, which is what makes an
  update incremental. electron-updater derives its URL by appending `.blockmap`
  to the installer URL, so it has to come from the same origin the installer is
  *addressed* at;
* the installer itself.

Only the first two are stored here (395 bytes and 115 KB). The installer is 110 MB
and pushing that from a CI runner to a server in another country measured ~28 KB/s
- about an hour per release. So `/updates/<installer>` answers a **302 to the
GitHub release asset** instead: the client follows it with its range request
intact, which is exactly what the GitHub provider does with release assets. The
bytes come from GitHub, the block map next to them comes from us, and incremental
updates work without a 110 MB upload.

The files are written by the release pipeline into `~/<slug>/updates/`, which
`deploy/compose.server.yaml` mounts read-only at `/app/updates`.

    GET /updates/latest.yml                                 the feed (404 if none)
    GET /updates/QuickLaunch-Setup-1.2.6-x64.exe.blockmap   the block map (local)
    GET /updates/QuickLaunch-Setup-1.2.6-x64.exe            302 -> GitHub release
"""

from __future__ import annotations

import re

from fastapi import APIRouter, status
from fastapi.responses import FileResponse, RedirectResponse, Response

from app.config import get_settings
from app.deps import api_error

# Not in the OpenAPI docs: it is not part of the public API surface, it is what a
# desktop client polls and downloads.
router = APIRouter(prefix="/updates", tags=["updates"], include_in_schema=False)

# Artifact names are ours (electron-builder's output), never user input: refuse
# anything that could climb out of the directory or look like a path.
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


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


def _feed_field(name: str) -> str:
    """Read one `key: value` line out of the published feed.

    The feed is written by `scripts/make_feed.py` in a fixed shape, and this reads
    exactly one key from it - a YAML dependency for that would be a poor trade.
    """
    path = get_settings().updates_file
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}:"):
            return line.split(":", 1)[1].strip()
    return ""


def _installer_name() -> str:
    """The installer file name the published feed points at."""
    installer_url = _feed_field("path")
    return installer_url.rsplit("/", 1)[-1] if installer_url else ""


@router.get("/{name}")
async def update_artifact(name: str) -> Response:
    """A file we host, or a redirect to the release asset we do not."""
    if not SAFE_NAME.match(name):
        raise api_error("not_found", "No such update artifact.", status.HTTP_404_NOT_FOUND)

    directory = get_settings().updates_file.parent
    candidate = directory / name
    if candidate.is_file():
        return FileResponse(candidate)

    # The installer is not stored here - see the module docstring. Redirect only
    # the one name the feed actually points at, so this cannot be used as an open
    # redirect to arbitrary URLs.
    if name == _installer_name():
        target = _feed_field("github_url")
        if target.startswith("https://"):
            return RedirectResponse(target, status_code=status.HTTP_302_FOUND)

    raise api_error("not_found", "No such update artifact.", status.HTTP_404_NOT_FOUND)
