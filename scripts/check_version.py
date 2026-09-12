#!/usr/bin/env python
"""Refuse a release whose version would reach nobody.

The desktop client updates itself by reading `latest.yml` from the newest GitHub
release and comparing versions: electron-updater only accepts a version strictly
HIGHER than the one it is running. So a tag that repeats or lowers the version
does not fail loudly on the user's machine - it just never arrives. That failure
mode is invisible from this side, which is why it is worth a hard gate in CI.

    python scripts/check_version.py --version 1.1.0 --repo owner/name
    python scripts/check_version.py --version 0.0.1 --repo owner/name   # exits 1

Drafts are ignored: the updater cannot see them, so they must not set the bar.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request


def version_key(value: str) -> tuple[tuple[int, int, int], bool]:
    """Sortable key: numeric parts first, then release-outranks-prerelease."""
    core = value.split("-")[0].split(".")
    numbers = tuple(int(part) if part.isdigit() else 0 for part in (core + ["0", "0"])[:3])
    is_release = "-" not in value
    return numbers, is_release


def published_versions(repo: str, token: str) -> list[str]:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases?per_page=100",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "quicklaunch-pipeline",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        releases = json.load(response)
    return [
        release["tag_name"].lstrip("v")
        for release in releases
        if not release["draft"] and release["tag_name"].lstrip("v")[:1].isdigit()
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True, help="the version being released, without the v")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--token", default=os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", ""))
    parser.add_argument(
        "--fail-if-not-newer",
        action="store_true",
        default=True,
        help="exit non-zero when the version is not newer (the CI behaviour)",
    )
    args = parser.parse_args()

    if not args.repo:
        print("::error::no repository given (--repo or GITHUB_REPOSITORY)")
        return 2
    if not args.token:
        print("::error::no token available (GH_TOKEN / GITHUB_TOKEN)")
        return 2

    published = published_versions(args.repo, args.token)
    if not published:
        print("no published releases yet; nothing to compare against")
        return 0

    highest = max(published, key=version_key)
    print(f"published highest: {highest}")
    print(f"this version     : {args.version}")

    if version_key(args.version) <= version_key(highest):
        print(
            f"::error::{args.version} is not newer than the published {highest}. "
            "Installed desktop clients only accept a HIGHER version, so this release "
            "would reach nobody. Bump the version and tag again."
        )
        return 1

    print(f"::notice::{args.version} is newer than the published {highest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
