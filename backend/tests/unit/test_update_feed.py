"""The update feed the desktop client reads.

Two ways to break auto-update silently, both covered here:

* the feed advertises a version that is not the one being released - installed
  clients then compare against the wrong number and either update forever or
  never;
* the file URL inside the feed stops being resolvable - the client sees "an
  update is available" and then fails to download it.

The feed is no longer published as a release asset (a release carries exactly one
file, the installer), so `make_feed.py` is what stands between the packaged
`latest.yml` and what clients actually read.
"""

from __future__ import annotations

import pytest

# scripts/ is on sys.path because the test bootstrap imports app.billing.fake.
from make_feed import build_feed, parse_feed

pytestmark = pytest.mark.unit

PACKAGED = """version: 1.2.3
files:
  - url: QuickLaunch-Setup-1.2.3-x64.exe
    sha512: 1J9nysN9Kutxa6HOjeaNLNZj0x6p92rnEJqWLvmlcclJtGb0y7m4I2PTXn7tbbzG3K0dmf0dKxSeHXKhhmw5nQ==
    size: 111716043
path: QuickLaunch-Setup-1.2.3-x64.exe
sha512: 1J9nysN9Kutxa6HOjeaNLNZj0x6p92rnEJqWLvmlcclJtGb0y7m4I2PTXn7tbbzG3K0dmf0dKxSeHXKhhmw5nQ==
releaseDate: '2026-09-13T07:10:36.917Z'
"""


class TestParsingThePackagedFeed:
    def test_version_and_file_entry_are_read(self) -> None:
        version, files = parse_feed(PACKAGED)
        assert version == "1.2.3"
        assert len(files) == 1
        assert files[0]["url"] == "QuickLaunch-Setup-1.2.3-x64.exe"
        assert files[0]["size"] == "111716043"

    def test_the_checksum_survives_parsing(self) -> None:
        _, files = parse_feed(PACKAGED)
        assert files[0]["sha512"].startswith(
            "1J9nysN9Kutxa6HOjeaNLNZj0x6p92rnEJqWLvmlcclJtGb0y7m4I2PTXn7tbbzG3K0dmf0dKxSeHXKhhmw5nQ=="
        )


class TestBuildingThePublishedFeed:
    def test_file_urls_become_absolute(self) -> None:
        feed = build_feed(PACKAGED, "1.2.3", "owner/name")
        assert (
            "url: https://github.com/owner/name/releases/download/v1.2.3/QuickLaunch-Setup-1.2.3-x64.exe"
            in feed
        )

    def test_the_checksum_and_size_are_preserved(self) -> None:
        """Dropping sha512 would make the client trust whatever it downloaded."""
        feed = build_feed(PACKAGED, "1.2.3", "owner/name")
        assert (
            "sha512: 1J9nysN9Kutxa6HOjeaNLNZj0x6p92rnEJqWLvmlcclJtGb0y7m4I2PTXn7tbbzG3K0dmf0dKxSeHXKhhmw5nQ=="
            in feed
        )
        assert "size: 111716043" in feed

    def test_the_legacy_top_level_shape_is_emitted_too(self) -> None:
        feed = build_feed(PACKAGED, "1.2.3", "owner/name")
        assert feed.splitlines()[-2].startswith("path: https://github.com/")
        assert feed.splitlines()[-1].startswith("sha512: ")

    def test_a_mismatched_version_is_refused(self) -> None:
        """The feed decides what installed clients compare against."""
        with pytest.raises(SystemExit, match="advertises 1.2.3"):
            build_feed(PACKAGED, "1.2.4", "owner/name")

    def test_an_empty_feed_is_refused(self) -> None:
        with pytest.raises(SystemExit, match="no files entry"):
            build_feed("version: 1.2.3\n", "1.2.3", "owner/name")
