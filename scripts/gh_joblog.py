#!/usr/bin/env python
"""Fetch a workflow job's log over the API, without the web UI.

GitHub redirects these to pre-signed blob storage; the signed URL rejects our
Authorization header, so the redirect is followed manually and the token is
dropped. Transient SSL/EOF errors are retried.

    python scripts/gh_joblog.py --job-id 123456 --tail 60
    python scripts/gh_joblog.py --job-id 123456 --grep "error|FAILED"
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


def fetch(job_id: int, attempts: int = 5) -> str:
    url = f"https://api.github.com/repos/{REPO}/actions/jobs/{job_id}/logs"
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url)
            request.add_header("Authorization", "Bearer " + token())
            request.add_header("User-Agent", "quicklaunch-demo")

            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
                    # Strip the auth header: the pre-signed URL signs its own.
                    plain = urllib.request.Request(newurl)
                    plain.add_header("User-Agent", "quicklaunch-demo")
                    return plain

            opener = urllib.request.build_opener(NoRedirect)
            with opener.open(request, timeout=120) as response:
                return response.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last = error
            if attempt < attempts:
                time.sleep(min(2**attempt, 20))
    raise SystemExit(f"could not fetch job {job_id} after {attempts} attempts: {last}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", type=int, required=True)
    parser.add_argument("--grep", default=r"error|Error|ERROR|FAILED|Traceback|failed")
    parser.add_argument("--tail", type=int, default=30)
    parser.add_argument("--context", type=int, default=1)
    parser.add_argument("--all", action="store_true", help="print the whole log")
    args = parser.parse_args()

    text = fetch(args.job_id)
    lines = [re.sub(r"^\S+Z\s?", "", line).rstrip() for line in text.replace("\r", "").splitlines()]

    if args.all:
        print("\n".join(lines))
        return 0

    pattern = re.compile(args.grep)
    hits = [i for i, line in enumerate(lines) if pattern.search(line)]
    if not hits:
        print("(no matching lines)")
        return 0
    shown: set[int] = set()
    for index in hits[-2:]:
        start = max(0, index - args.context)
        end = min(len(lines), index + args.tail)
        print("---")
        for position in range(start, end):
            if position in shown:
                continue
            shown.add(position)
            print(lines[position].encode("ascii", errors="replace").decode("ascii"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
