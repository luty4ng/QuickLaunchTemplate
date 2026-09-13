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


FEED_WITH_REDIRECT = """version: 9.9.9
files:
  - url: https://example.test/updates/App-Setup-9.9.9-x64.exe
    sha512: abc
    size: 1234
path: https://example.test/updates/App-Setup-9.9.9-x64.exe
sha512: abc
github_url: https://github.com/owner/name/releases/download/v9.9.9/App-Setup-9.9.9-x64.exe
"""


def _publish(monkeypatch, tmp_path, text: str = FEED_WITH_REDIRECT):
    feed = tmp_path / "latest.yml"
    feed.write_text(text, encoding="utf-8")
    monkeypatch.setattr(get_settings(), "updates_file", feed)
    return feed


class TestTheInstallerRedirect:
    """The installer is 110 MB and is not stored here; it is redirected.

    The block map *is* stored here, and it has to be reachable at the same origin
    the feed points at - that is the whole reason the feed does not simply point at
    GitHub in the first place.
    """

    def test_the_installer_redirects_to_the_release_asset(self, client, monkeypatch, tmp_path) -> None:
        _publish(monkeypatch, tmp_path)

        response = client.get("/updates/App-Setup-9.9.9-x64.exe", follow_redirects=False)

        assert response.status_code == 302, response.text
        assert response.headers["location"] == (
            "https://github.com/owner/name/releases/download/v9.9.9/App-Setup-9.9.9-x64.exe"
        )

    def test_only_the_name_in_the_feed_is_redirected(self, client, monkeypatch, tmp_path) -> None:
        """Otherwise this would be an open redirect to any URL in the feed."""
        _publish(monkeypatch, tmp_path)

        response = client.get("/updates/Some-Other-Installer.exe", follow_redirects=False)

        assert response.status_code == 404

    def test_a_name_that_looks_like_a_path_is_refused(self, client, monkeypatch, tmp_path) -> None:
        _publish(monkeypatch, tmp_path)

        for name in ("..%2F..%2Fetc%2Fpasswd", ".hidden", "sub%2Fdir"):
            response = client.get(f"/updates/{name}", follow_redirects=False)
            assert response.status_code == 404, f"{name} -> {response.status_code}"

    def test_a_stored_block_map_is_served(self, client, monkeypatch, tmp_path) -> None:
        _publish(monkeypatch, tmp_path)
        (tmp_path / "App-Setup-9.9.9-x64.exe.blockmap").write_bytes(b"blockmap-bytes")

        response = client.get("/updates/App-Setup-9.9.9-x64.exe.blockmap")

        assert response.status_code == 200, response.text
        assert response.content == b"blockmap-bytes"

    def test_the_block_map_answers_range_requests(self, client, monkeypatch, tmp_path) -> None:
        """Differential download is a range request; a 200 here means no delta."""
        _publish(monkeypatch, tmp_path)
        (tmp_path / "App-Setup-9.9.9-x64.exe.blockmap").write_bytes(b"0123456789")

        response = client.get("/updates/App-Setup-9.9.9-x64.exe.blockmap", headers={"Range": "bytes=0-3"})

        assert response.status_code == 206, response.text
        assert response.content == b"0123"
