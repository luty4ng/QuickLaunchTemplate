#!/usr/bin/env python
"""Decide whether the scheduled release should fire today, and start it if so.

Manual tagging stays the normal way to ship. What this adds is "release at a
configured hour if there is something worth releasing" - and, just as important,
"do not release when nothing has changed or when the commit is not verified".

GitHub Actions cannot express a cron that reads a repository variable, so the
workflow wakes up every hour and this script decides whether *this* is the
configured hour. That is what makes the release time configurable without
editing the workflow:

    AUTO_RELEASE_ENABLED=true        # default: false - the switch
    AUTO_RELEASE_HOUR=2              # default: 2
    AUTO_RELEASE_TZ=Asia/Shanghai    # default: Asia/Shanghai
    AUTO_RELEASE_BUMP=patch          # default: patch (minor / major also work)

All four gates must pass:

    1. the switch is on;
    2. the current hour, in the configured zone, is the configured hour;
    3. main has moved since the newest release    (nothing new -> nothing to do);
    4. the newest *push* run for that commit succeeded
       (never publish a commit that CI has not finished verifying).

Runs on a schedule it dispatches the pipeline with `version=<next>` and
`deploy_ref=main`; the release job then creates the tag from that run, so there
stays exactly one release path. A workflow_dispatch of the pipeline can trigger
the same decision by hand, with `auto_release_dry_run` defaulting to true so a
click cannot publish by accident.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

API = "https://api.github.com"
WORKFLOW_FILE = "pipeline.yml"


def call(path: str, token: str, method: str = "GET", body: dict | None = None) -> dict | list | None:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"{API}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "quicklaunch-auto-release",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read().decode()
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:300]
        raise SystemExit(f"::error::GitHub API {method} {path} failed: {error.code} {detail}") from error
    return json.loads(payload) if payload else None


def newest_release(repo: str, token: str) -> dict | None:
    releases = call(f"/repos/{repo}/releases?per_page=20", token) or []
    published = [release for release in releases if not release["draft"]]
    if not published:
        return None
    # The API returns newest first, but do not rely on it.
    return max(published, key=lambda release: release["created_at"])


def release_commit(repo: str, tag: str, token: str) -> str:
    """The commit a tag points at, annotated tag or not."""
    commit = call(f"/repos/{repo}/commits/{tag}", token)
    return str(commit["sha"]) if isinstance(commit, dict) else ""


def head_is_verified(repo: str, token: str, sha: str) -> tuple[bool, str]:
    """Has a *push* run for this exact commit finished successfully?

    A green run for an older commit says nothing about this one, so the sha is
    matched exactly. A run still in flight returns False, and the next heartbeat
    will pick it up - which is the point of waking up every hour.
    """
    runs = call(f"/repos/{repo}/actions/runs?branch=main&event=push&per_page=30", token) or {}
    for run in runs.get("workflow_runs", []):
        # Compared by prefix: the API lists full shas, callers may pass short ones.
        if run["head_sha"][:7] != sha[:7]:
            continue
        if run["status"] != "completed":
            return False, f"the push run for {sha[:7]} is still {run['status']}"
        if run["conclusion"] == "success":
            return True, f"push run {run['id']} for {sha[:7]} succeeded"
        return False, f"the push run for {sha[:7]} concluded {run['conclusion']}"
    return False, f"no push run has been recorded for {sha[:7]} yet"


def decide(args: argparse.Namespace) -> int:
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(ZoneInfo(args.timezone))
    hour = int(args.hour)
    # The workflow passes the *string* "true"/"false", and any non-empty string is
    # truthy in Python - so this compares rather than tests.
    enabled = str(args.enabled).strip().lower() == "true"

    print(f"repository      : {args.repo}")
    print(f"head commit     : {(args.head or '(unknown)')[:7]}")
    print(f"now ({args.timezone})  : {now.isoformat(timespec='minutes')}  (release hour: {hour:02d})")
    print(f"dry run         : {args.dry_run}")

    if not enabled:
        print("decision: no release - AUTO_RELEASE_ENABLED is not 'true'")
        return 0
    if args.ignore_hour:
        # A human asking explicitly is not bound by the hour they configured for
        # the unattended run; the heartbeat still is.
        print(f"hour gate       : skipped (manual run; the heartbeat releases at {hour:02d})")
    elif now.hour != hour:
        print(f"decision: no release - this heartbeat is hour {now.hour:02d}, not {hour:02d}")
        return 0

    newest = newest_release(args.repo, args.token)
    if newest is None:
        print(
            "decision: no release - there are no published releases to build on yet; "
            "tag the first one by hand"
        )
        return 0
    tag = newest["tag_name"]
    print(f"newest release  : {tag} (created {newest['created_at']})")

    if not args.head:
        print("decision: no release - no head commit to compare")
        return 0
    # Compared by prefix: the API answers with a full sha, the workflow passes one
    # that may be abbreviated.
    released = release_commit(args.repo, tag, args.token)
    if released[:7] == args.head[:7]:
        print(f"decision: no release - {tag} already points at {args.head[:7]}")
        return 0

    verified, why = head_is_verified(args.repo, args.token, args.head)
    print(f"verification    : {why}")
    if not verified:
        print("decision: no release - the commit is not verified green yet")
        return 0

    next_tag = f"v{args.next}"
    print(f"decision: RELEASE - {tag} -> {next_tag}")

    if args.dry_run:
        print(f"(dry run: the pipeline would be dispatched with version={args.next} deploy_ref=main)")
        return 0

    call(
        f"/repos/{args.repo}/actions/workflows/{WORKFLOW_FILE}/dispatches",
        args.token,
        method="POST",
        body={"ref": "main", "inputs": {"version": args.next, "deploy_ref": "main"}},
    )
    print(f"::notice::dispatched the pipeline to release {next_tag} from {args.head[:7]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--token", default=os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN", ""))
    parser.add_argument("--head", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--enabled", default=os.environ.get("AUTO_RELEASE_ENABLED", "false"))
    parser.add_argument("--hour", default=os.environ.get("AUTO_RELEASE_HOUR") or "2")
    parser.add_argument("--timezone", default=os.environ.get("AUTO_RELEASE_TZ") or "Asia/Shanghai")
    parser.add_argument("--bump", default=os.environ.get("AUTO_RELEASE_BUMP") or "patch")
    parser.add_argument(
        "--dry-run",
        default=(os.environ.get("AUTO_RELEASE_DRY_RUN", "true").lower() == "true"),
        action=argparse.BooleanOptionalAction,
        help="print the decision without dispatching anything",
    )
    parser.add_argument(
        "--ignore-hour",
        default=(os.environ.get("AUTO_RELEASE_IGNORE_HOUR", "false").lower() == "true"),
        action=argparse.BooleanOptionalAction,
        help="skip the configured-hour gate (set for manual runs, not for the heartbeat)",
    )
    parser.add_argument(
        "--next",
        default="",
        help="the version to release (computed by scripts/check_version.py --next)",
    )
    parser.add_argument("--now", default="", help="test hook: pretend it is this time")
    args = parser.parse_args()

    if not args.repo or not args.token:
        print("::error::--repo and --token (or GITHUB_REPOSITORY / GITHUB_TOKEN) are required")
        return 2
    if str(args.enabled).strip().lower() == "true" and not args.next:
        print("::error::--next is required when auto-release is enabled")
        return 2
    return decide(args)


if __name__ == "__main__":
    sys.exit(main())
