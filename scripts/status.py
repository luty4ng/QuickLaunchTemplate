#!/usr/bin/env python
"""One-screen status of the newest (or a specific) pipeline run.

python scripts/status.py                 # newest run
python scripts/status.py --run-id 12345
python scripts/status.py --watch         # refresh every 20s until it settles
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gh import REPO, call  # noqa: E402

BAD = {"failure", "cancelled", "timed_out", "startup_failure", "action_required"}


def render(run: dict) -> tuple[bool, str]:
    jobs = call("GET", f"/repos/{REPO}/actions/runs/{run['id']}/jobs")["jobs"]
    lines = [
        f"run #{run['id']}  {run['head_sha'][:7]}  {run['status']}/{run['conclusion']}  {run['html_url']}",
    ]
    done = run["status"] == "completed"
    for job in jobs:
        lines.append(f"  [{job['status']}/{job['conclusion']}] {job['name']}")
        if job["conclusion"] in BAD:
            for step in job["steps"] or []:
                if step["conclusion"] in BAD:
                    lines.append(f"      failed step: {step['name']}")
    return done, "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()

    while True:
        if args.run_id:
            run = call("GET", f"/repos/{REPO}/actions/runs/{args.run_id}")
        else:
            run = call("GET", f"/repos/{REPO}/actions/runs?per_page=1")["workflow_runs"][0]
        done, text = render(run)
        print(text, flush=True)
        if done or not args.watch:
            return 0 if run["conclusion"] == "success" else 1
        print("  ... waiting\n", flush=True)
        time.sleep(20)


if __name__ == "__main__":
    sys.exit(main())
