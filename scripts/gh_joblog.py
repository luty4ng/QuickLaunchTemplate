#!/usr/bin/env python
"""Fetch a workflow job's log, following the redirect to blob storage manually.

GitHub answers this endpoint with a 302 to a pre-signed URL. The signed URL
rejects the Authorization header, and urllib's default redirect handling keeps
it, which surfaces as a confusing SSL EOF. The token is dropped on the hop.

    python scripts/gh_joblog.py --job-id 123456 --grep "error" --tail 30
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gh import REPO, token  # noqa: E402


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        # Never inherit headers: the pre-signed URL authenticates itself.
        return urllib.request.Request(newurl)


def plain_get(url: str) -> str:
    request = urllib.request.Request(url)
    request.add_header("User-Agent", "quicklaunch-demo")
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch(job_id: int, attempts: int = 4) -> str:
    url = f"https://api.github.com/repos/{REPO}/actions/jobs/{job_id}/logs"
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url)
            request.add_header("Authorization", "Bearer " + token())
            request.add_header("User-Agent", "quicklaunch-demo")
            opener = urllib.request.build_opener(_NoRedirect)
            try:
                with opener.open(request, timeout=120) as response:
                    return response.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as error:
                if error.code not in (301, 302, 303, 307, 308):
                    raise
                location = error.headers.get("Location")
                if not location:
                    raise
                return plain_get(location)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last = error
            if attempt < attempts:
                time.sleep(min(2**attempt, 20))
    raise SystemExit(f"could not fetch job {job_id} after {attempts} attempts: {last}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", type=int, required=True)
    parser.add_argument("--grep", default=r"error|Error|ERROR|FAILED|failed|Traceback")
    parser.add_argument("--tail", type=int, default=30)
    parser.add_argument("--context", type=int, default=2)
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    text = fetch(args.job_id)
    lines = [re.sub(r"^\S+Z\s?", "", line).rstrip() for line in text.replace("\r", "").splitlines()]

    if args.all:
        for line in lines:
            print(line.encode("ascii", errors="replace").decode("ascii"))
        return 0

    pattern = re.compile(args.grep)
    hits = [i for i, line in enumerate(lines) if pattern.search(line)]
    if not hits:
        print(f"(no lines matching {args.grep!r})")
        return 0
    shown: set[int] = set()
    for index in hits[-2:]:
        print("---")
        for position in range(max(0, index - args.context), min(len(lines), index + args.tail)):
            if position in shown:
                continue
            shown.add(position)
            print(lines[position].encode("ascii", errors="replace").decode("ascii"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
