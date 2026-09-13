#!/usr/bin/env python
"""Turn electron-builder's `latest.yml` into the feed the app serves.

electron-builder writes a feed whose file entry is a bare name, because it
expects to sit in the same place as the binary:

    files:
      - url: QuickLaunch-Setup-1.2.3-x64.exe
        sha512: ...
        size: 111716043

That works when the feed lives inside the release (relative URLs resolve against
the release's download path). Ours is served from the application, so the URL has
to be absolute - and it points at the **same origin the blockmap is on**, because
electron-updater derives the blockmap URL from it (`<file url>.blockmap`) to fetch
only the changed bytes. Serving the installer from anywhere else silently turns
every update back into a full 107 MB download.

    files:
      - url: https://example.com/updates/QuickLaunch-Setup-1.2.3-x64.exe
        sha512: ...
        size: 111716043

`sha512` and `size` are copied verbatim - they are what the client verifies the
download with, and inventing or dropping them would either fail every update or
silently weaken the check.

    python scripts/make_feed.py --input desktop/release/latest.yml \\
        --version 1.2.3 --base-url https://example.com/updates \\
        --output updates/latest.yml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def parse_feed(text: str) -> tuple[str, list[dict[str, str]]]:
    """A deliberately tiny reader for the fixed shape electron-builder writes.

    Pulling in a YAML library would add a dependency to a pipeline step that
    otherwise needs only the standard library, and this is not general YAML: it is
    `version:` plus a list of file entries with `url`/`sha512`/`size`. Top-level
    keys (`path`, `sha512`, `releaseDate`) are ignored on purpose - they are the
    legacy shape, and the rewrite emits them again from the entries.
    """
    version = ""
    files: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent == 0:
            current = None
            key, _, value = stripped.partition(":")
            if key.strip() == "version":
                version = value.strip().strip("'\"")
            continue
        if stripped.startswith("- "):
            current = {}
            files.append(current)
            stripped = stripped[2:]
        if current is None:
            continue
        key, _, value = stripped.partition(":")
        current[key.strip()] = value.strip().strip("'\"")
    return version, files


def build_feed(text: str, version: str, base_url: str) -> str:
    """Rewrite the feed so every file URL is absolute.

    `base_url` is where the installers (and their blockmaps) are served from -
    normally `<domain>/updates`, the same place this feed is served from. The
    blockmap URL is not written into the feed: electron-updater appends
    `.blockmap` to whatever URL is here, which is precisely why the installer has
    to live on the same origin as the blockmap.
    """
    advertised, files = parse_feed(text)
    if advertised and advertised != version:
        # The version in the feed decides what installed clients compare against;
        # a mismatch here would publish a feed that points at the wrong build.
        raise SystemExit(f"::error::the packaged feed advertises {advertised} but this release is {version}")
    if not files:
        raise SystemExit("::error::the input feed has no files entry - nothing to publish")
    base = base_url.rstrip("/")
    for entry in files:
        name = entry.get("url", "")
        if not name:
            raise SystemExit("::error::the input feed has a file entry without a url")
        if name.startswith(("http://", "https://")):
            continue
        entry["url"] = f"{base}/{name}"

    lines = [f"version: {version}", "files:"]
    for entry in files:
        lines.append(f"  - url: {entry['url']}")
        for key in ("sha512", "size"):
            if key in entry:
                lines.append(f"    {key}: {entry[key]}")
    # `path` and the top-level `sha512` are the legacy pre-2.16 shape; a modern
    # client reads `files`. Emitting both keeps very old installs working.
    first = files[0]
    lines.append(f"path: {first['url']}")
    if "sha512" in first:
        lines.append(f"sha512: {first['sha512']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--input", required=True, help="the latest.yml electron-builder produced")
    parser.add_argument("--output", required=True, help="where to write the publishable feed")
    parser.add_argument("--version", required=True, help="version being released, without the v")
    parser.add_argument(
        "--base-url",
        required=True,
        help="where the installers are served from, e.g. https://example.com/updates",
    )
    args = parser.parse_args()

    source = Path(args.input)
    if not source.is_file():
        print(
            f"::error::{source} does not exist - the packaging step did not produce a feed", file=sys.stderr
        )
        return 1
    feed = build_feed(source.read_text(encoding="utf-8"), args.version, args.base_url)

    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(feed, encoding="utf-8", newline="\n")
    print(f"wrote {target}:")
    print(feed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
