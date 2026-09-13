#!/usr/bin/env python
"""Post-deployment smoke test.

Runs against a real, already-running deployment - not against the test client.
It answers one question: *is the artifact that we just shipped usable?*

    1. GET /api/health must be 200 within the budget (default 1000 ms)
    2. the SPA shell must be served at /
    3. full user journey: register -> list -> create -> patch -> delete
    4. cross-user isolation must still hold on the live deployment
    5. logout must invalidate the session

Exit code 0 = shippable, 1 = roll back.

    python scripts/smoke.py --base-url http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

TIMEOUT = 10
CHECKS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))
    print(
        f"{'PASS' if ok else 'FAIL'}  {name}{f' :: {detail}' if detail else ''}",
        flush=True,
    )


class Session:
    """Tiny cookie-aware HTTP client - no third-party dependency needed."""

    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip("/")
        self.cookie: str | None = None

    def request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        *,
        timeout: int = TIMEOUT,
        base_override: str | None = None,
    ) -> tuple[int, object, float]:
        data = json.dumps(payload).encode() if payload is not None else None
        origin = base_override.rstrip("/") if base_override else self.base
        request = urllib.request.Request(f"{origin}{path}", data=data, method=method)
        request.add_header("Accept", "application/json")
        if data:
            request.add_header("Content-Type", "application/json")
        if self.cookie:
            request.add_header("Cookie", self.cookie)

        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
                status = response.status
                set_cookie = response.headers.get("Set-Cookie")
        except urllib.error.HTTPError as error:
            body = error.read()
            status = error.code
            set_cookie = error.headers.get("Set-Cookie")
        elapsed_ms = (time.perf_counter() - started) * 1000

        if set_cookie:
            self.cookie = set_cookie.split(";", 1)[0]
        try:
            parsed = json.loads(body) if body else None
        except json.JSONDecodeError:
            parsed = body.decode(errors="replace")
        return status, parsed, elapsed_ms

    def get_text(self, path: str) -> tuple[int, str]:
        request = urllib.request.Request(f"{self.base}{path}")
        request.add_header("Accept", "text/html")
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return response.status, response.read().decode(errors="replace")
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode(errors="replace")


def check_health(base_url: str, budget_ms: int) -> bool:
    session = Session(base_url)
    try:
        status, body, elapsed = session.request("GET", "/api/health")
    except Exception as error:  # noqa: BLE001 - any transport failure is a failed deploy
        record("health endpoint answers", False, f"{type(error).__name__}: {error}")
        return False

    ok = status == 200 and isinstance(body, dict) and body.get("status") == "ok"
    record(
        "health endpoint answers",
        ok,
        f"status={status} body={body} in {elapsed:.0f} ms",
    )
    record(
        f"health responds within {budget_ms} ms",
        elapsed <= budget_ms,
        f"{elapsed:.0f} ms",
    )
    return ok


def check_spa(base_url: str) -> None:
    session = Session(base_url)
    status, html = session.get_text("/")
    record(
        "web shell is served at /",
        status == 200 and '<div id="root">' in html,
        f"status={status} bytes={len(html)}",
    )


def check_journey(base_url: str) -> str | None:
    """The whole product in one pass. Returns the user's email on success."""
    email = f"smoke+{uuid.uuid4().hex[:10]}@example.com"
    password = "smoke-test-password"
    user = Session(base_url)

    status, body, _ = user.request("POST", "/api/auth/register", {"email": email, "password": password})
    record("register returns 201", status == 201, f"status={status} body={body}")
    if status != 201:
        return None

    status, body, _ = user.request("GET", "/api/todos")
    record(
        "new account starts empty",
        status == 200 and body == [],
        f"status={status} body={body}",
    )

    status, created, _ = user.request("POST", "/api/todos", {"title": "smoke: created"})
    ok = status == 201 and isinstance(created, dict) and created.get("title") == "smoke: created"
    record("create todo returns 201", ok, f"status={status} body={created}")
    if not ok or not isinstance(created, dict):
        return None
    todo_id = created["id"]

    status, listed, _ = user.request("GET", "/api/todos")
    record(
        "created todo is listed",
        status == 200 and isinstance(listed, list) and [t["id"] for t in listed] == [todo_id],
        f"status={status}",
    )

    status, patched, _ = user.request("PATCH", f"/api/todos/{todo_id}", {"done": True})
    record(
        "marking done persists",
        status == 200 and isinstance(patched, dict) and patched.get("done") is True,
        f"status={status} body={patched}",
    )

    status, _, _ = user.request("DELETE", f"/api/todos/{todo_id}")
    record("delete returns 204", status == 204, f"status={status}")

    status, listed, _ = user.request("GET", "/api/todos")
    record(
        "todo list is empty again",
        status == 200 and listed == [],
        f"status={status} body={listed}",
    )

    status, _, _ = user.request("POST", "/api/auth/logout")
    record("logout returns 204", status == 204, f"status={status}")

    status, body, _ = user.request("GET", "/api/todos")
    record("session is dead after logout", status == 401, f"status={status} body={body}")

    return email


def check_isolation(base_url: str) -> None:
    """Prove on the live deployment that one user cannot touch another's data."""
    suffix = uuid.uuid4().hex[:10]
    alice = Session(base_url)
    bob = Session(base_url)
    alice.request(
        "POST",
        "/api/auth/register",
        {"email": f"alice+{suffix}@example.com", "password": "isolation-pw"},
    )
    bob.request(
        "POST",
        "/api/auth/register",
        {"email": f"bob+{suffix}@example.com", "password": "isolation-pw"},
    )

    _, created, _ = alice.request("POST", "/api/todos", {"title": "alice only"})
    if not isinstance(created, dict):
        record("isolation: alice can create a todo", False, f"body={created}")
        return
    todo_id = created["id"]

    status, body, _ = bob.request("PATCH", f"/api/todos/{todo_id}", {"done": True})
    record("isolation: foreign PATCH is 404", status == 404, f"status={status} body={body}")

    status, _, _ = bob.request("DELETE", f"/api/todos/{todo_id}")
    record("isolation: foreign DELETE is 404", status == 404, f"status={status}")

    _, alice_list, _ = alice.request("GET", "/api/todos")
    record(
        "isolation: alice's todo survived untouched",
        isinstance(alice_list, list) and len(alice_list) == 1,
        f"body={alice_list}",
    )


def check_login(base_url: str, email: str | None) -> None:
    if not email:
        record(
            "log in again with the same credentials",
            False,
            "registration failed earlier",
        )
        return
    user = Session(base_url)
    status, body, _ = user.request(
        "POST", "/api/auth/login", {"email": email, "password": "smoke-test-password"}
    )
    record(
        "log in with the same credentials",
        status == 200,
        f"status={status} body={body}",
    )

    status, body, _ = user.request("POST", "/api/auth/login", {"email": email, "password": "wrong-password"})
    record("wrong password is rejected", status == 401, f"status={status}")


def check_billing(base_url: str) -> None:
    """The payment path, end to end, against the running deployment.

    Three states, each reported as what it is rather than as a pass or a failure
    in disguise:

    * **no provider configured** - a supported way to run this template. The
      quota still applies (it does not depend on Stripe), and checkout must fail
      loudly with 503 rather than pretend to work.
    * **a fake provider** - the paying half runs: a free account is capped at its
      limit, checkout returns a provider url, a **signed** webhook lifts the cap,
      and cancelling drops the plan without touching the user's existing todos.
    * **real Stripe** - needs credentials and a human with a card, so it records
      what it can and stops.

    The deployment gate calls this against the public url, so the first state
    must not fail the deployment: billing being off is a configuration, not a
    defect.
    """
    suffix = uuid.uuid4().hex[:10]
    user = Session(base_url)
    user.request(
        "POST",
        "/api/auth/register",
        {"email": f"billing+{suffix}@example.com", "password": "billing-password"},
    )

    status, me, _ = user.request("GET", "/api/billing/me")
    if status != 200 or not isinstance(me, dict):
        record("billing: /billing/me answers", False, f"status={status} body={me}")
        return
    record(
        "billing: /billing/me answers",
        True,
        f"plan={me.get('plan')} enabled={me.get('billing_enabled')}",
    )

    enabled = bool(me.get("billing_enabled"))
    if enabled:
        record("billing: payment provider is configured", True, "billing_enabled=true")
    else:
        record(
            "billing: payment provider is configured",
            True,
            "skipped - this deployment runs without a payment provider",
        )
        status, body, _ = user.request("POST", "/api/billing/checkout", {"plan": "plus"})
        code = body.get("error", {}).get("code") if isinstance(body, dict) else None
        record(
            "billing: checkout fails loudly when unconfigured",
            status == 503 and code == "billing_unavailable",
            f"status={status} code={code}",
        )

    quota = me.get("quota") or {}
    limit = quota.get("limit")
    record(
        "billing: free plan has a finite limit",
        isinstance(limit, int) and limit > 0,
        f"limit={limit}",
    )
    if not isinstance(limit, int) or limit <= 0:
        return

    for index in range(limit):
        created = user.request("POST", "/api/todos", {"title": f"billing smoke {index}"})[0]
        if created != 201:
            record("billing: free plan accepts todos up to the limit", False, f"stopped at {index}")
            return
    record("billing: free plan accepts todos up to the limit", True, f"{limit} created")

    status, body, _ = user.request("POST", "/api/todos", {"title": "over the limit"})
    error_code = body.get("error", {}).get("code") if isinstance(body, dict) else None
    record(
        "billing: the limit is enforced with 402",
        status == 402 and error_code == "quota_exceeded",
        f"status={status} code={error_code}",
    )

    if not enabled:
        return

    status, checkout, _ = user.request("POST", "/api/billing/checkout", {"plan": "plus"})
    if status != 200 or not isinstance(checkout, dict) or not checkout.get("url"):
        record("billing: checkout returns a provider url", False, f"status={status} body={checkout}")
        return
    url = str(checkout["url"])
    record("billing: checkout returns a provider url", True, url)

    if "fake" not in url:
        record("billing: payment path", True, "skipped - a real provider is configured")
        return

    # The fake provider's control endpoint records the payment and delivers the
    # signed webhooks Stripe would send - which is what actually grants the plan.
    provider = fake_origin(url)
    session_id = url.rsplit("/", 1)[-1]
    status, paid, _ = user.request(
        "POST", "/__control/pay", {"session_id": session_id}, base_override=provider
    )
    if status != 200 or not isinstance(paid, dict):
        record("billing: provider records the payment", False, f"status={status} body={paid}")
        return
    record("billing: provider records the payment", True, f"subscription={paid.get('subscription')}")

    _, me_after, _ = user.request("GET", "/api/billing/me")
    plan = me_after.get("plan") if isinstance(me_after, dict) else None
    record("billing: the signed webhook lifted the plan", plan == "plus", f"plan={plan}")

    status, _, _ = user.request("POST", "/api/todos", {"title": "after upgrading"})
    record("billing: the limit is gone after upgrading", status == 201, f"status={status}")

    subscription = paid.get("subscription")
    if not subscription:
        return
    user.request("POST", "/__control/cancel", {"subscription_id": subscription}, base_override=provider)
    _, me_cancelled, _ = user.request("GET", "/api/billing/me")
    plan_after = me_cancelled.get("plan") if isinstance(me_cancelled, dict) else None
    record("billing: cancelling drops the plan", plan_after == "free", f"plan={plan_after}")

    _, todos, _ = user.request("GET", "/api/todos")
    kept = len(todos) if isinstance(todos, list) else -1
    record(
        "billing: cancelling keeps the user's todos",
        kept >= limit,
        f"{kept} todos remain (the free limit is {limit})",
    )


def fake_origin(checkout_url: str) -> str:
    """The fake provider's origin, taken from the url it handed back."""
    parsed = urllib.parse.urlsplit(checkout_url)
    return f"{parsed.scheme}://{parsed.netloc}"


def check_update_feed(base_url: str) -> None:
    """The desktop update path, from the outside.

    Installed clients read `/updates/latest.yml` on every launch to decide whether
    to offer an update, then download the installer it points at - and, to avoid
    re-downloading 110 MB every time, fetch only the blocks that changed using
    HTTP range requests against that installer and its `.blockmap`.

    Three things to prove, each of which fails silently on its own:

    * the feed answers and names an absolute installer URL;
    * that installer is actually downloadable (a feed pointing at a file that was
      pruned or never published looks fine until a user clicks update);
    * the server honours range requests (otherwise every update quietly becomes a
      full download - no error anywhere, updates just get slow).

    A 404 is not a failure: a deployment that has never published a desktop
    release legitimately has no feed. The pipeline's `update-feed` job is the gate
    that a *published* release has a usable one.
    """
    status, body, _ = Session(base_url).request("GET", "/updates/latest.yml", timeout=15)
    if status == 404:
        record(
            "update feed: no desktop release published yet",
            True,
            "skipped - the feed answers 404 until a release is published",
        )
        return
    if status != 200 or not isinstance(body, str):
        record("update feed: readable", False, f"status={status}")
        return

    version = next(
        (line.split(":", 1)[1].strip() for line in body.splitlines() if line.startswith("version:")), ""
    )
    installer = next(
        (line.split("url:", 1)[1].strip() for line in body.splitlines() if line.strip().startswith("- url:")),
        "",
    )
    record(
        "update feed: readable and usable",
        bool(version) and installer.startswith("http"),
        f"version={version or '(missing)'} absolute_url={installer.startswith('http')}",
    )
    if not installer.startswith("http"):
        return

    # One ranged GET answers both questions: 404 means the feed points at a file
    # that is not there, 200 means it is there but ranges are ignored (so every
    # update silently becomes a full download), 206 means incremental updates can
    # work. A HEAD is deliberately not used: GitHub answers those with a redirect
    # that curl reports rather than follows, which says nothing about the file.
    code = _curl_code(["-L", "-o", "/dev/null", "-r", "0-0", installer])
    record(
        "update feed: the installer it points at is downloadable",
        code in ("200", "206"),
        f"status={code or 'no response'}",
    )
    record(
        "update feed: range requests are honoured (incremental updates)",
        code == "206",
        f"status={code or 'no response'} (206 = only changed blocks, 200 = always the whole file)",
    )


def _curl_code(arguments: list[str]) -> str:
    """The status code of a curl call, or '' when curl is unavailable or failed."""
    try:
        result = subprocess.run(
            ["curl", "-sS", "--max-time", "30", "-w", "%{http_code}", *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ""
    output = result.stdout.strip()
    return output.splitlines()[-1] if output else ""


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--health-budget-ms", type=int, default=1000)
    parser.add_argument(
        "--skip-spa",
        action="store_true",
        help="API-only mode (no web client in the image)",
    )
    parser.add_argument(
        "--skip-billing",
        action="store_true",
        help="skip the payment checks (they only run against a fake provider)",
    )
    args = parser.parse_args()

    print(f"smoke testing {args.base_url}", flush=True)

    # Give a cold container a moment before declaring it dead.
    for attempt in range(1, 31):
        try:
            Session(args.base_url).request("GET", "/api/health", timeout=5)
            break
        except Exception:  # noqa: BLE001
            if attempt == 30:
                record(
                    "deployment accepts connections",
                    False,
                    "no response after 30 attempts",
                )
                return 1
            time.sleep(2)

    if not check_health(args.base_url, args.health_budget_ms):
        return 1
    if not args.skip_spa:
        check_spa(args.base_url)
    email = check_journey(args.base_url)
    check_login(args.base_url, email)
    check_isolation(args.base_url)
    if not args.skip_billing:
        check_billing(args.base_url)
    check_update_feed(args.base_url)

    failed = [name for name, ok, _ in CHECKS if not ok]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed", flush=True)
    if failed:
        print("failed checks:", *failed, sep="\n  - ", flush=True)
        return 1
    print("SMOKE OK", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
