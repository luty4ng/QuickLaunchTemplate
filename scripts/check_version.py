#!/usr/bin/env python
"""Refuse a release whose version would reach nobody.

The desktop client updates itself by reading `latest.yml` from the newest GitHub
release and comparing versions: electron-updater only accepts a version strictly
HIGHER than the one it is running. So a tag that repeats or lowers the version
does not fail loudly on the user's machine - it just never arrives. That failure
mode is invisible from this side, which is why it is worth a hard gate in CI.

    python scripts/check_version.py --version 1.1.0 --repo owner/name
    python scripts/check_version.py --version 0.0.1 --repo owner/name   # exits 1
    python scripts/check_version.py --next patch --repo owner/name      # prints 1.2.2

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


def next_version(highest: str | None, bump: str) -> str:
    """The version to release next, given the highest published one.

    The automatic release uses this: nobody wants to hand-pick a patch number at
    2am, but the version still has to move strictly upward or the desktop clients
    never see it.
    """
    parts = (highest or "0.0.0").split("-")[0].split(".")
    while len(parts) < 3:
        parts.append("0")
    major, minor, patch = (int(part) if part.isdigit() else 0 for part in parts[:3])
    if bump == "major":
        major, minor, patch = major + 1, 0, 0
    elif bump == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    return f"{major}.{minor}.{patch}"


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
    parser.add_argument("--version", help="the version being released, without the v")
    parser.add_argument(
        "--next",
        choices=("patch", "minor", "major"),
        help="print the next version instead of checking one (used by the automatic release)",
    )
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--token", default=os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", ""))
    parser.add_argument(
        "--fail-if-not-newer",
        action="store_true",
        default=True,
        help="exit non-zero when the version is not newer (the CI behaviour)",
    )
    args = parser.parse_args()

    if bool(args.version) == bool(args.next):
        print("::error::pass exactly one of --version or --next")
        return 2
    if not args.repo:
        print("::error::no repository given (--repo or GITHUB_REPOSITORY)")
        return 2
    if not args.token:
        print("::error::no token available (GH_TOKEN / GITHUB_TOKEN)")
        return 2

    published = published_versions(args.repo, args.token)
    highest = max(published, key=version_key) if published else None

    if args.next:
        # Printed to stdout on its own line so the caller can capture it; the
        # reasoning goes to stderr to keep the two apart.
        print(f"highest published: {highest or '(none)'}", file=sys.stderr)
        print(next_version(highest, args.next))
        return 0

    if not published:
        print("no published releases yet; nothing to compare against")
        return 0

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
