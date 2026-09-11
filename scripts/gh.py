#!/usr/bin/env python
"""Minimal GitHub API client for driving this demo's repo and workflows.

Auth comes from the local git credential store (the same thing `git push`
uses), so there is no token in the repo, in the shell history, or in this file.

    python scripts/gh.py whoami
    python scripts/gh.py ensure-repo --owner luty4ng --name QuickLaunchTemplate
    python scripts/gh.py runs --limit 5
    python scripts/gh.py wait --sha <sha> --timeout 900
    python scripts/gh.py logs --run-id 12345
    python scripts/gh.py set-secret NAME --value-file path

Only ever talks to api.github.com, and only about the repository named by
`--repo`/`GITHUB_REPOSITORY`.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

API = "https://api.github.com"
REPO = os.environ.get("GITHUB_REPOSITORY", "luty4ng/QuickLaunchTemplate")


def token() -> str:
    """Read the GitHub token out of the git credential helper."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
    out = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
        env=env,
        check=True,
    ).stdout
    for line in out.splitlines():
        if line.startswith("password="):
            return line[len("password=") :]
    raise SystemExit("no token in the git credential store")


def call(
    method: str,
    path: str,
    body: dict | None = None,
    *,
    raw: bool = False,
    ok: tuple[int, ...] = (200,),
    attempts: int = 5,
):
    url = path if path.startswith("http") else API + path
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token()}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("User-Agent", "quicklaunch-demo")
    if data:
        request.add_header("Content-Type", "application/json")

    # api.github.com is reached over a link that drops connections now and then;
    # retry transport errors and 5xx instead of failing an overnight run.
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = response.read()
                if raw:
                    return payload.decode(errors="replace")
                return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")
            if error.code in ok:
                return json.loads(detail) if detail else {}
            if error.code >= 500 or error.code == 429:
                last = SystemExit(f"{method} {path} -> {error.code}: {detail[:200]}")
            else:
                raise SystemExit(f"{method} {path} -> {error.code}: {detail[:400]}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last = error
        if attempt < attempts:
            time.sleep(min(2**attempt, 30))
    raise SystemExit(f"{method} {path} failed after {attempts} attempts: {last}")


def cmd_whoami(_: argparse.Namespace) -> int:
    user = call("GET", "/user")
    print(f"token owner: {user['login']}")
    return 0


def cmd_ensure_repo(args: argparse.Namespace) -> int:
    full = f"{args.owner}/{args.name}"
    try:
        repo = call("GET", f"/repos/{full}")
        print(f"repo exists: {repo['full_name']} (private={repo['private']})")
    except SystemExit:
        repo = call(
            "POST",
            "/user/repos",
            {
                "name": args.name,
                "description": "QuickLaunch - one FastAPI backend, three clients (web/desktop/Android), delivered by one GitHub Actions pipeline.",
                "private": False,
                "has_issues": True,
                "has_wiki": False,
                "auto_init": False,
            },
            ok=(201,),
        )
        print(f"repo created: {repo['full_name']} -> {repo['html_url']}")
    actions = call("GET", f"/repos/{full}/actions/permissions")
    print(f"actions enabled={actions.get('enabled')} allowed_actions={actions.get('allowed_actions')}")
    return 0


def cmd_runs(args: argparse.Namespace) -> int:
    query = f"/repos/{REPO}/actions/runs?per_page={args.limit}"
    runs = call("GET", query)["workflow_runs"]
    for run in runs:
        print(
            f"#{run['id']} {run['name']} [{run['event']}] {run['head_branch']} "
            f"{run['head_sha'][:7]} -> {run['status']}/{run['conclusion']} "
            f"{run['created_at']} {run['html_url']}"
        )
    if not runs:
        print("no runs yet")
    return 0


def cmd_jobs(args: argparse.Namespace) -> int:
    jobs = call("GET", f"/repos/{REPO}/actions/runs/{args.run_id}/jobs")["jobs"]
    for job in jobs:
        print(f"[{job['status']}/{job['conclusion']}] {job['name']}")
        for step in job["steps"] or []:
            print(f"    - {step['status']}/{step['conclusion']}: {step['name']}")
    return 0


def cmd_wait(args: argparse.Namespace) -> int:
    """Poll the runs for a commit until every run finishes."""
    deadline = time.time() + args.timeout
    watched: dict[int, str] = {}
    while True:
        runs = call("GET", f"/repos/{REPO}/actions/runs?per_page=50")["workflow_runs"]
        mine = [r for r in runs if r["head_sha"].startswith(args.sha) or r["head_sha"] == args.sha]
        if not mine:
            if time.time() > deadline:
                print("no workflow run appeared for that commit")
                return 2
            time.sleep(10)
            continue
        for run in mine:
            watched[run["id"]] = f"{run['name']} -> {run['status']}/{run['conclusion']}"
        pending = [r for r in mine if r["status"] != "completed"]
        if not pending:
            break
        if time.time() > deadline:
            print(f"timed out; last seen: {json.dumps(watched, indent=2)}")
            return 2
        time.sleep(15)

    failed = 0
    for run in mine:
        conclusion = run["conclusion"]
        print(f"#{run['id']} {run['name']} ({run['head_sha'][:7]}) -> {conclusion}")
        if conclusion != "success":
            failed += 1
            for job in call("GET", f"/repos/{REPO}/actions/runs/{run['id']}/jobs")["jobs"]:
                if job["conclusion"] == "success":
                    continue
                print(f"  FAILED JOB: {job['name']} -> {job['conclusion']}")
                for step in job["steps"] or []:
                    if step["conclusion"] not in ("success", "skipped", None):
                        print(f"    step {step['conclusion']}: {step['name']}")
    return 1 if failed else 0


def cmd_logs(args: argparse.Namespace) -> int:
    """Print job logs; falls back to the tail of each failed step's log."""
    jobs = call("GET", f"/repos/{REPO}/actions/runs/{args.run_id}/jobs")["jobs"]
    for job in jobs:
        if args.failed_only and job["conclusion"] == "success":
            continue
        print(f"===== {job['name']} [{job['conclusion']}] =====")
        if not job["id"]:
            continue
        try:
            text = call("GET", f"/repos/{REPO}/actions/jobs/{job['id']}/logs", raw=True)
        except SystemExit as error:
            print(f"  (log unavailable: {error})")
            continue
        lines = text.splitlines()
        tail = lines[-args.tail :] if args.tail else lines
        print("\n".join(tail))
    return 0


def cmd_set_secret(args: argparse.Namespace) -> int:
    import base64

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key = call("GET", f"/repos/{REPO}/actions/secrets/public-key")
    with open(args.value_file, encoding="utf-8") as handle:
        value = handle.read().strip()
    public_key = serialization.load_pem_public_key(key["key"].encode())
    sealed = public_key.encrypt(value.encode(), padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    call("PUT", f"/repos/{REPO}/actions/secrets/{args.name}", {"encrypted_value": base64.b64encode(sealed).decode(), "key_id": key["key_id"]}, ok=(201, 204))
    print(f"secret {args.name} set")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("whoami").set_defaults(func=cmd_whoami)

    ensure = sub.add_parser("ensure-repo")
    ensure.add_argument("--owner", default=REPO.split("/")[0])
    ensure.add_argument("--name", default=REPO.split("/")[1])
    ensure.set_defaults(func=cmd_ensure_repo)

    runs = sub.add_parser("runs")
    runs.add_argument("--limit", type=int, default=10)
    runs.set_defaults(func=cmd_runs)

    jobs = sub.add_parser("jobs")
    jobs.add_argument("--run-id", type=int, required=True)
    jobs.set_defaults(func=cmd_jobs)

    wait = sub.add_parser("wait")
    wait.add_argument("--sha", required=True)
    wait.add_argument("--timeout", type=int, default=1800)
    wait.set_defaults(func=cmd_wait)

    logs = sub.add_parser("logs")
    logs.add_argument("--run-id", type=int, required=True)
    logs.add_argument("--tail", type=int, default=60)
    logs.add_argument("--failed-only", action="store_true")
    logs.set_defaults(func=cmd_logs)

    secret = sub.add_parser("set-secret")
    secret.add_argument("name")
    secret.add_argument("--value-file", required=True)
    secret.set_defaults(func=cmd_set_secret)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
