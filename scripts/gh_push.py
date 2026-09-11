#!/usr/bin/env python
"""Mirror the local repository to a GitHub repository over the REST API.

Used to create the deliberately-broken "gate check" branches as pull requests
without touching anyone's checkouts. Only ever writes to the repository named by
REPO.

    python scripts/gh_push.py --branch gate/type-error --from HEAD \
        --title "gate check: type error" --body-file note.md
"""

from __future__ import annotations

import argparse
import base64
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gh import REPO, call  # noqa: E402


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def changed_files(base: str) -> list[str]:
    return [line for line in git("diff", "--name-only", base).splitlines() if line]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--branch", required=True)
    parser.add_argument("--from", dest="base", default="main")
    parser.add_argument("--title", required=True)
    parser.add_argument("--body", default="Deliberately broken commit used to prove a pipeline gate.")
    parser.add_argument("--no-pr", action="store_true")
    args = parser.parse_args()

    parent = call("GET", f"/repos/{REPO}/git/ref/heads/{args.base}")["object"]["sha"]
    files = changed_files(args.base)
    if not files:
        raise SystemExit("no changes relative to the base branch")

    tree: list[dict] = []
    for path in files:
        content = Path(path).read_bytes()
        blob = call(
            "POST",
            f"/repos/{REPO}/git/blobs",
            {"content": base64.b64encode(content).decode(), "encoding": "base64"},
            ok=(201,),
        )
        tree.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        print(f"  staged {path}")

    new_tree = call("POST", f"/repos/{REPO}/git/trees", {"base_tree": parent, "tree": tree}, ok=(201,))
    message = f"{args.title}\n\n{args.body}\n"
    commit = call(
        "POST",
        f"/repos/{REPO}/git/commits",
        {"message": message, "tree": new_tree["sha"], "parents": [parent]},
        ok=(201,),
    )
    call(
        "POST",
        f"/repos/{REPO}/git/refs",
        {"ref": f"refs/heads/{args.branch}", "sha": commit["sha"]},
        ok=(201,),
    )
    print(f"branch {args.branch} -> {commit['sha'][:7]}")

    if not args.no_pr:
        pr = call(
            "POST",
            f"/repos/{REPO}/pulls",
            {"title": args.title, "head": args.branch, "base": args.base, "body": args.body},
            ok=(201,),
        )
        print(f"pull request #{pr['number']}: {pr['html_url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
