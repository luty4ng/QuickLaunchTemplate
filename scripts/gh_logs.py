#!/usr/bin/env python
"""Download the logs of a workflow run and print a filtered view.

    python scripts/gh_logs.py --run-id 12345 --grep "FAILED|Error" --tail 40
    python scripts/gh_logs.py --run-id 12345 --job backend          # one step file
    python scripts/gh_logs.py --run-id 12345 --list                 # what is inside

Windows consoles are not UTF-8, so everything is written to a UTF-8 file and
only ASCII-safe lines are echoed.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gh import REPO

CACHE = Path(__file__).resolve().parents[1] / ".cache"


def fetch(run_id: int) -> zipfile.ZipFile:
    url = f"https://api.github.com/repos/{REPO}/actions/runs/{run_id}/logs"
    request = urllib.request.Request(url)
    request.add_header("Authorization", "Bearer " + __import__("gh").token())
    request.add_header("User-Agent", "quicklaunch-demo")
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read()
    CACHE.mkdir(exist_ok=True)
    archive = CACHE / f"logs-{run_id}.zip"
    archive.write_bytes(data)
    return zipfile.ZipFile(io.BytesIO(data))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--job", help="substring of the step file name to print")
    parser.add_argument("--grep", default=r"FAILED|ERROR|Error:|error:|Traceback|assert|##\[error\]")
    parser.add_argument("--tail", type=int, default=40)
    parser.add_argument("--desktop-only", action="store_true", help="print step names only")
    args = parser.parse_args()

    archive = fetch(args.run_id)
    names = archive.namelist()

    if args.list:
        for name in names:
            print(name)
        return 0

    pattern = re.compile(args.grep)
    for name in names:
        if args.job and args.job.lower() not in name.lower():
            continue
        text = archive.read(name).decode("utf-8", errors="replace")
        # Strip the ISO timestamp GitHub prefixes every line with.
        lines = [re.sub(r"^\S+Z\s?", "", line).rstrip() for line in text.splitlines()]
        hits = [i for i, line in enumerate(lines) if pattern.search(line)]
        if not hits:
            continue
        print(f"\n===== {name} =====")
        shown: set[int] = set()
        for index in hits[-3:]:
            start = max(0, index - 3)
            end = min(len(lines), index + args.tail)
            for position in range(start, end):
                if position in shown:
                    continue
                shown.add(position)
                safe = lines[position].encode("ascii", errors="replace").decode("ascii")
                print(safe)
            print("  ---")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.exit(main())
