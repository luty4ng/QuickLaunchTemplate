#!/usr/bin/env python
"""Print the failure annotations GitHub shows on a run's summary page.

More reliable than log downloads: the log endpoints redirect to pre-signed blob
storage that is flaky and unavailable until the entire run finishes, while
annotations are plain API data available as soon as a job ends.

    python scripts/gh_annotations.py --sha c47ba5a
    python scripts/gh_annotations.py            # newest run's commit
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gh import REPO, call  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sha", help="commit to inspect (default: newest run)")
    parser.add_argument("--all", action="store_true", help="include successful checks")
    args = parser.parse_args()

    sha = args.sha
    if not sha:
        sha = call("GET", f"/repos/{REPO}/actions/runs?per_page=1")["workflow_runs"][0]["head_sha"]

    checks = call("GET", f"/repos/{REPO}/commits/{sha}/check-runs?per_page=100")["check_runs"]
    print(f"commit {sha[:7]}: {len(checks)} check runs")
    total = 0
    for check in checks:
        if not args.all and check["conclusion"] == "success":
            continue
        annotations = call("GET", f"/repos/{REPO}/check-runs/{check['id']}/annotations")
        if not annotations:
            continue
        for item in annotations:
            total += 1
            location = f" ({item['path']}:{item.get('start_line')})" if item.get("path") else ""
            print(f"\n[{check['name']}] {item['annotation_level']}: {item.get('title') or ''}{location}")
            print(f"  {item['message']}")
    if not total:
        print("(no annotations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
