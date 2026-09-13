"""The desktop update feed is served by the app, not published as a release asset.

A release carries exactly one file (the installer), so installed clients cannot
learn about new versions from the release page any more. They read
`/updates/latest.yml` instead. Two things must hold, and both are silent failures
if they break: the route must not be swallowed by the SPA catch-all, and it must
answer 404 (not an HTML page) while nothing has been published.
"""

from __future__ import annotations

import pytest
from app.config import get_settings

pytestmark = pytest.mark.integration

FEED = """version: 9.9.9
files:
  - url: https://github.com/owner/name/releases/download/v9.9.9/App-Setup-9.9.9-x64.exe
    sha512: abc
    size: 1234
"""


def test_nothing_published_yet_answers_404(client) -> None:
    response = client.get("/updates/latest.yml")
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "no_update_feed"


def test_a_published_feed_is_served_verbatim(client, monkeypatch, tmp_path) -> None:
    feed = tmp_path / "latest.yml"
    feed.write_text(FEED, encoding="utf-8")
    monkeypatch.setattr(get_settings(), "updates_file", feed)

    response = client.get("/updates/latest.yml")

    assert response.status_code == 200, response.text
    assert "version: 9.9.9" in response.text
    assert "releases/download/v9.9.9/App-Setup-9.9.9-x64.exe" in response.text
    # A client parses YAML; an HTML error page or a JSON envelope would look like
    # "no update feed" to it, which is exactly the silent failure being avoided.
    assert response.headers["content-type"].startswith("text/yaml")


def test_the_route_wins_over_the_spa(client, monkeypatch, tmp_path) -> None:
    """Otherwise an unknown path returns index.html with a 200 and clients break."""
    feed = tmp_path / "latest.yml"
    feed.write_text(FEED, encoding="utf-8")
    monkeypatch.setattr(get_settings(), "updates_file", feed)

    response = client.get("/updates/latest.yml")

    assert "<html" not in response.text.lower()
