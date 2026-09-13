#!/usr/bin/env python
"""One place that knows what this project is called, so ten places do not guess.

`project.env` holds the project's identity: domain, repository, slug, app ids.
Everything that can read it at runtime does (the deploy script, the workflow's
shell steps, the compose files). The few files that cannot - `package.json` and
`capacitor.config.json` are data formats with no interpolation, and a workflow's
`env:` block is resolved before any step runs - are pinned by hand and **checked**
here instead:

    python scripts/project_env.py show       # the resolved values
    python scripts/project_env.py export     # KEY=VALUE lines, for $GITHUB_ENV
    python scripts/project_env.py check      # fail if anything drifted (CI runs this)
    python scripts/project_env.py bootstrap --repo owner/name --domain x.y --write

The check is the important part. Renaming a project by hand has failure modes
that are not obvious: a wrong `REPO_SLUG` fails the deployment loudly (fine),
but a wrong electron-builder `publish.owner` makes desktop auto-update silently
stop working, which nobody notices until users stop receiving versions at all.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / "project.env"

REQUIRED = (
    "PROJECT_NAME",
    "PROJECT_SLUG",
    "REPO_OWNER",
    "REPO_NAME",
    "APP_DOMAIN",
    "DESKTOP_APP_ID",
    "MOBILE_APP_ID",
)

# Files that are allowed to contain the project's literals. Everything else must
# derive them, so a stray one is reported rather than tolerated.
CHECKED_FILES = (
    ".github/workflows/pipeline.yml",
    "compose.yaml",
    ".env.example",
    "deploy/compose.server.yaml",
    "deploy/deploy.sh",
    "desktop/package.json",
    "desktop/app-config.json",
    "mobile/capacitor.config.json",
    "web/index.html",
    "web/package.json",
    "web/package-lock.json",
    "web/src/App.tsx",
)


def load(path: Path = ENV_FILE) -> dict[str, str]:
    if not path.exists():
        raise SystemExit(f"::error::{path} is missing - it is the source of truth for project identity")
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    missing = [key for key in REQUIRED if not values.get(key)]
    if missing:
        raise SystemExit(f"::error::project.env is missing values for: {', '.join(missing)}")
    return values


def derived(values: dict[str, str]) -> dict[str, str]:
    slug = values["PROJECT_SLUG"]
    owner = values["REPO_OWNER"]
    name = values["REPO_NAME"]
    return {
        **values,
        "REPO_SLUG": f"{owner}/{name}",
        # GHCR rejects upper case, which is why this is not simply REPO_NAME.
        "IMAGE_NAME": name.lower(),
        "IMAGE_REPO": f"ghcr.io/{owner}/{name.lower()}",
        "APP_DIR": slug,
        "SMOKE_URL": f"https://{values['APP_DOMAIN']}",
        "COMPOSE_PROJECT_NAME": slug,
    }


def tracked_files() -> list[str]:
    return [path for path in CHECKED_FILES if (ROOT / path).exists()]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------
def check(values: dict[str, str]) -> list[str]:
    """Returns a list of human-readable problems; empty means everything agrees."""
    v = derived(values)
    problems: list[str] = []

    def require(relative: str, needle: str, why: str) -> None:
        text = read(relative)
        if needle not in text:
            problems.append(f"{relative}: expected to find {needle!r} ({why})")

    def forbid(relative: str, needle: str, why: str) -> None:
        if not needle:
            return
        text = read(relative)
        if needle in text:
            line = next(
                (number for number, content in enumerate(text.splitlines(), 1) if needle in content),
                "?",
            )
            problems.append(f"{relative}:{line}: still contains {needle!r} ({why})")

    workflow = ".github/workflows/pipeline.yml"
    # The workflow's env: block is evaluated before any step can read project.env,
    # so the image name has to be written out here - and therefore checked.
    if not re.search(
        r"^\s*IMAGE:\s*ghcr\.io/\$\{\{ github\.repository_owner \}\}/" + re.escape(v["IMAGE_NAME"]) + r"\s*$",
        read(workflow),
        re.MULTILINE,
    ):
        problems.append(
            f"{workflow}: expected `IMAGE: ghcr.io/${{{{ github.repository_owner }}}}/{v['IMAGE_NAME']}` "
            "(the env: block cannot read project.env, so this line is pinned and checked)"
        )
    require(workflow, "scripts/project_env.py export", "the workflow must load project.env into $GITHUB_ENV")
    forbid(workflow, v["APP_DOMAIN"], "the public url must come from project.env")
    forbid(workflow, f"~/{v['APP_DIR']}/", "the deploy directory must come from project.env")
    forbid(workflow, v["REPO_SLUG"], "the repository must come from project.env")

    require(
        "compose.yaml",
        f"name: ${{COMPOSE_PROJECT_NAME:-{v['COMPOSE_PROJECT_NAME']}}}",
        "compose project name falls back to project.env's slug",
    )
    if read("compose.yaml").count(f"${{APP_IMAGE:-{v['IMAGE_REPO']}:latest}}") != 2:
        problems.append(
            f"compose.yaml: both the app and migrate services must default APP_IMAGE to "
            f"{v['IMAGE_REPO']}:latest"
        )

    require(
        ".env.example", f"APP_IMAGE={v['IMAGE_REPO']}:latest", "local runs must point at this project's image"
    )
    require(
        ".env.example", f"COMPOSE_PROJECT_NAME={v['COMPOSE_PROJECT_NAME']}", "compose needs the project name"
    )
    require(".env.example", f"APP_DOMAIN={v['APP_DOMAIN']}", "the server overlay needs the domain")

    # Traefik labels: the domain is interpolated, but label *keys* are not - so
    # the router name is a literal that has to match the slug. And Host() takes
    # exactly one argument in Traefik v3: a two-argument form is accepted by
    # nobody and produces a 404 on the public url (it did, once).
    require(
        "deploy/compose.server.yaml",
        f"traefik.http.routers.{v['PROJECT_SLUG']}.rule: Host(`${{APP_DOMAIN}}`)",
        "the Traefik rule interpolates the domain from project.env, and its key "
        "cannot be interpolated so it must equal the slug",
    )
    forbid("deploy/compose.server.yaml", v["APP_DOMAIN"], "the domain must come from project.env")

    require("deploy/deploy.sh", '. "$APP_DIR/project.env"', "the deploy script loads project.env")
    forbid("deploy/deploy.sh", v["APP_DOMAIN"], "the domain must come from project.env")
    forbid("deploy/deploy.sh", v["REPO_SLUG"], "the repository must come from project.env")

    desktop = json.loads(read("desktop/package.json"))
    build = desktop.get("build", {})
    publish = build.get("publish", {})
    expectations = {
        "homepage": f"https://github.com/{v['REPO_SLUG']}",
        "repository.url": f"https://github.com/{v['REPO_SLUG']}.git",
        "build.appId": v["DESKTOP_APP_ID"],
        "build.productName": v["PROJECT_NAME"],
        "build.publish.owner": v["REPO_OWNER"],
        "build.publish.repo": v["REPO_NAME"],
    }
    actual = {
        "homepage": desktop.get("homepage"),
        "repository.url": desktop.get("repository", {}).get("url"),
        "build.appId": build.get("appId"),
        "build.productName": build.get("productName"),
        "build.publish.owner": publish.get("owner"),
        "build.publish.repo": publish.get("repo"),
    }
    for field, expected in expectations.items():
        if actual[field] != expected:
            problems.append(
                f"desktop/package.json: {field} is {actual[field]!r}, expected {expected!r} "
                + ("(a wrong publish.owner stops desktop auto-update silently)" if "publish" in field else "")
            )

    # The desktop app reads its update feed from this project's own domain; the
    # value is baked into the package, so a stale one would point installed
    # clients at the previous project's server - and they would look up to date
    # forever, which is the silent failure this whole check exists for.
    app_config = json.loads(read("desktop/app-config.json"))
    expected_feed = f"https://{v['APP_DOMAIN']}/updates"
    if app_config.get("updateFeedUrl") != expected_feed:
        problems.append(
            f"desktop/app-config.json: updateFeedUrl is {app_config.get('updateFeedUrl')!r}, "
            f"expected {expected_feed!r}"
        )

    mobile = json.loads(read("mobile/capacitor.config.json"))
    if mobile.get("appId") != v["MOBILE_APP_ID"]:
        problems.append(
            f"mobile/capacitor.config.json: appId is {mobile.get('appId')!r}, "
            f"expected {v['MOBILE_APP_ID']!r} "
            "(a stale appId collides with the previous project's package)"
        )
    if mobile.get("appName") != v["PROJECT_NAME"]:
        problems.append(
            f"mobile/capacitor.config.json: appName is {mobile.get('appName')!r}, "
            f"expected {v['PROJECT_NAME']!r}"
        )

    # The web bundle cannot read project.env, so its brand strings are literals -
    # the same bargain as the desktop package. These are only *cosmetic* when
    # stale (wrong name in the tab title and the header), which is exactly why
    # nobody notices: they are checked so a rename cannot quietly miss them.
    expected_web_name = f"{v['PROJECT_SLUG']}-web"
    package = json.loads(read("web/package.json"))
    if package.get("name") != expected_web_name:
        problems.append(
            f"web/package.json: name is {package.get('name')!r}, expected {expected_web_name!r}"
        )
    lock = json.loads(read("web/package-lock.json"))
    if lock.get("name") != expected_web_name:
        problems.append(
            f"web/package-lock.json: name is {lock.get('name')!r}, expected {expected_web_name!r} "
            "(kept in step with package.json so `npm ci` stays consistent)"
        )
    require(
        "web/index.html",
        f"<title>{v['PROJECT_NAME']}</title>",
        "the browser tab must show this project's name",
    )
    require(
        "web/src/App.tsx",
        f"const APP_NAME = '{v['PROJECT_NAME']}'",
        "the header brand is a literal in the bundle; keep it as one rewritable line",
    )

    return problems


def cmd_check(values: dict[str, str]) -> int:
    problems = check(values)
    if problems:
        print("::error::project identity has drifted from project.env:")
        for problem in problems:
            print(f"  - {problem}")
        print(
            "\nFix by hand, or run: python scripts/project_env.py bootstrap "
            "--repo <owner/name> --domain <domain> --write"
        )
        return 1
    print(f"project identity agrees with project.env ({len(tracked_files())} files checked)")
    return 0


def cmd_show(values: dict[str, str]) -> int:
    width = max(len(key) for key in derived(values))
    for key, value in derived(values).items():
        print(f"{key.ljust(width)}  {value}")
    return 0


def cmd_export(values: dict[str, str]) -> int:
    for key, value in derived(values).items():
        print(f"{key}={value}")
    return 0


# ---------------------------------------------------------------------------
# bootstrap: rewrite the pinned files for a new project
# ---------------------------------------------------------------------------
def cmd_bootstrap(args: argparse.Namespace, values: dict[str, str]) -> int:
    new = dict(values)
    if args.name:
        new["PROJECT_NAME"] = args.name
    if args.slug:
        new["PROJECT_SLUG"] = args.slug
    if args.repo:
        owner, _, name = args.repo.partition("/")
        if not owner or not name:
            print("::error::--repo must look like owner/name")
            return 2
        new["REPO_OWNER"], new["REPO_NAME"] = owner, name
    if args.domain:
        new["APP_DOMAIN"] = args.domain
    if args.app_id:
        new["DESKTOP_APP_ID"] = args.app_id
        if args.app_id.endswith(".desktop"):
            new["MOBILE_APP_ID"] = args.app_id[: -len(".desktop")] + ".app"
        else:
            new["MOBILE_APP_ID"] = args.app_id
    elif args.slug:
        # A new slug means new app ids: keeping the old ones would collide with
        # the previous project's package on an installed device.
        new["DESKTOP_APP_ID"] = f"dev.{args.slug}.desktop"
        new["MOBILE_APP_ID"] = f"dev.{args.slug}.app"

    old, fresh = derived(values), derived(new)
    # Longest first, so `ghcr.io/o/name` is replaced before the bare `name`, and
    # `owner/name` before `name` alone. The ordering is not cosmetic: a rehearsal
    # of this migration turned `QuickLaunchTemplate` into `LaunchKitTemplate`,
    # because the *project name* is a prefix of the *repository name* and the
    # shorter key was applied first. The check at the end caught it - which is
    # what the check is for - but the map should not need it to be correct.
    replacements = {
        old["APP_DOMAIN"]: fresh["APP_DOMAIN"],
        old["IMAGE_REPO"]: fresh["IMAGE_REPO"],
        old["REPO_SLUG"]: fresh["REPO_SLUG"],
        old["IMAGE_NAME"]: fresh["IMAGE_NAME"],
        old["REPO_NAME"]: fresh["REPO_NAME"],
        old["REPO_OWNER"]: fresh["REPO_OWNER"],
        old["DESKTOP_APP_ID"]: fresh["DESKTOP_APP_ID"],
        old["MOBILE_APP_ID"]: fresh["MOBILE_APP_ID"],
        old["PROJECT_NAME"]: fresh["PROJECT_NAME"],
        old["COMPOSE_PROJECT_NAME"]: fresh["COMPOSE_PROJECT_NAME"],
    }
    ordered = sorted(
        ((k, val) for k, val in replacements.items() if k and k != val),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )

    changed: list[str] = []
    for relative in CHECKED_FILES:
        path = ROOT / relative
        if not path.exists():
            print(f"  skip {relative} (not present)")
            continue
        text = original = path.read_text(encoding="utf-8")
        for before, after in ordered:
            text = text.replace(before, after)
        if text != original:
            changed.append(relative)
            if args.write:
                path.write_text(text, encoding="utf-8", newline="\n")

    env_text = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
    for key in REQUIRED:
        env_text = re.sub(rf"^{key}=.*$", f"{key}={fresh[key]}", env_text, flags=re.MULTILINE)
    if args.write:
        ENV_FILE.write_text(env_text, encoding="utf-8", newline="\n")

    print("files that would change:" if not args.write else "files rewritten:")
    for relative in changed or ["(none)"]:
        print(f"  - {relative}")
    print("\nproject.env:")
    for key in REQUIRED:
        if values.get(key) != fresh[key]:
            print(f"  {key}: {values.get(key)} -> {fresh[key]}")
        else:
            print(f"  {key}: {fresh[key]} (unchanged)")

    if not args.write:
        print("\n(dry run - pass --write to apply)")
        return 0

    print()
    status = cmd_check(new)
    print(
        "\nRemaining manual steps for a new project:\n"
        "  1. DNS: point the domain at the server\n"
        "  2. Server: create ~/<slug>/, write .env (JWT_SECRET, POSTGRES_PASSWORD, APP_IMAGE),\n"
        "     put the deploy public key in ~/.ssh/authorized_keys\n"
        "  3. GitHub: secret DEPLOY_SSH_KEY, variables DEPLOY_HOST / DEPLOY_USER\n"
        "  4. Optional: Stripe keys in the server .env, AUTO_RELEASE_* variables (see report/AUTOMATION.md)\n"
        "  5. Push a tag v0.1.0 - the pipeline validates, publishes and deploys"
    )
    return status


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show", help="print the resolved values")
    sub.add_parser("export", help="print KEY=VALUE lines for $GITHUB_ENV")
    sub.add_parser("check", help="verify the pinned files agree with project.env")

    boot = sub.add_parser("bootstrap", help="rewrite the pinned files for a new project")
    boot.add_argument("--name", help="display name, e.g. 'Acme Launch'")
    boot.add_argument("--slug", help="lower-case short name, e.g. 'acmelaunch'")
    boot.add_argument("--repo", help="owner/name")
    boot.add_argument("--domain", help="public domain")
    boot.add_argument("--app-id", help="desktop app id, e.g. dev.acme.desktop")
    boot.add_argument("--write", action="store_true", help="apply the changes (default: dry run)")

    args = parser.parse_args()
    values = load()
    if args.command == "show":
        return cmd_show(values)
    if args.command == "export":
        return cmd_export(values)
    if args.command == "check":
        return cmd_check(values)
    return cmd_bootstrap(args, values)


if __name__ == "__main__":
    sys.exit(main())
