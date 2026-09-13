"""Serve the desktop update feed from the application itself.

The feed (`latest.yml`) is what an installed desktop client reads to learn
whether a newer version exists and where to fetch it. electron-updater's GitHub
provider looks for that file **among the assets of the newest release**, which is
why it used to be published there - and why the downloads page carried a YAML
file next to the installer.

Serving it from here keeps the promise "a release contains exactly one file, the
installer": the `url` inside the feed points at that release asset (an absolute
URL), while the feed itself is a ~400 byte file this app hands out. The blockmap
is not published either, so updates download the full installer; with the feed
served from a different origin than the binary, differential download has nothing
to work with anyway.

    GET /updates/latest.yml     the feed (404 while nothing has been published)
"""

from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import FileResponse

from app.config import get_settings
from app.deps import api_error

# Not in the OpenAPI docs: it is not part of the public API surface, it is a file
# a desktop client polls.
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
