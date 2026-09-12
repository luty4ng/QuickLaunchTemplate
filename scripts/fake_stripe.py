#!/usr/bin/env python
"""A stand-in for Stripe, for end-to-end tests without an account.

The smoke test needs to prove the whole billing path works - checkout, payment,
a signed webhook, entitlement, and the quota lifting - against a **running
deployment**. Doing that with real Stripe would need credentials, a public
webhook url and a human clicking a card form, so this serves the same HTTP
surface the app calls:

    POST /v1/checkout/sessions          create a checkout session
    POST /v1/billing_portal/sessions    create a portal session
    GET  /v1/subscriptions/{id}         retrieve a subscription
    POST /__control/pay                 test hook: mark a session paid
    POST /__control/cancel              test hook: cancel and notify
    POST /__control/reset               test hook: forget everything

The control endpoints exist because a real payer is a human. They are prefixed
`__control`, and the script refuses to start as `--env production`.

Imports nothing but the standard library, so it can run on a bare interpreter -
which is how the deployment smoke test starts it, without the backend's
dependencies installed. The logic lives in `fake_stripe_core.py`, shared with the
test suite's in-memory gateway so the two cannot disagree about event shapes.

    python scripts/fake_stripe.py --port 12194 \\
        --webhook-target http://127.0.0.1:8000/api/billing/webhook

It signs with `STRIPE_WEBHOOK_SECRET` when that is set - the same variable the
application verifies with - so the two sides agree by construction rather than by
two matching literals.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_stripe_core import FakeStripeState, default_webhook_secret  # noqa: E402

# Above Windows' reserved TCP range (12094-12193 on the machine this was written
# on): binding inside a reserved range fails with WinError 10013, which reads
# like "port in use" but is not.
DEFAULT_PORT = 12194

# Where checkout urls point, so the smoke test can find the control endpoints from
# the url the application hands back. Set in main().
PUBLIC_BASE = f"http://127.0.0.1:{DEFAULT_PORT}"
WEBHOOK_SECRET = default_webhook_secret()
STATE = FakeStripeState(base_url=PUBLIC_BASE, webhook_secret=WEBHOOK_SECRET)
WEBHOOK_TARGET: str | None = None

SESSION_RE = re.compile(r"^/v1/checkout/sessions/([^/]+)$")
SUBSCRIPTION_RE = re.compile(r"^/v1/subscriptions/([^/]+)$")


def fingerprint(secret: str) -> str:
    """A short, non-reversible id for a secret, safe to print in a public log."""
    return hashlib.sha256(secret.encode()).hexdigest()[:8]


def new_state() -> FakeStripeState:
    """A fresh state that keeps signing with the configured secret."""
    return FakeStripeState(base_url=PUBLIC_BASE, webhook_secret=WEBHOOK_SECRET)


def form(payload: bytes, content_type: str) -> dict[str, str]:
    """Stripe's API is form-encoded; flatten it into a dict of strings."""
    text = payload.decode() if payload else ""
    if "application/json" in content_type:
        return {str(k): str(v) for k, v in json.loads(text or "{}").items()}
    parsed = urllib.parse.parse_qs(text, keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items()}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length") or 0)
        return form(self.rfile.read(length), self.headers.get("Content-Type", ""))

    def _deliver(self, event: dict) -> tuple[int, str]:
        """POST a signed event to the application, exactly as Stripe would."""
        global STATE
        if not WEBHOOK_TARGET:
            return 0, "no webhook target configured"

        payload = json.dumps(event).encode()
        request = urllib.request.Request(
            WEBHOOK_TARGET,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Stripe-Signature": STATE.sign(payload),
                "User-Agent": "fake-stripe",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = response.read().decode(errors="replace")
                code = response.status
        except urllib.error.HTTPError as error:
            body, code = error.read().decode(errors="replace"), error.code
        except Exception as error:  # noqa: BLE001 - report, never crash the fake
            body, code = str(error), 0
        # Printed either way: "delivered" and "applied" are different things, and
        # a webhook that returns 200 while granting nothing is the failure this
        # whole script exists to catch.
        print(f"fake-stripe: delivered {event.get('type')} -> {code} {body[:200]}", flush=True)
        if code == 400 and "signature" in body:
            # The one mismatch that is invisible from the application's side: both
            # processes look correctly configured, and only the pair is wrong.
            print(
                "fake-stripe: HINT - the application rejected the signature. It verifies "
                f"with its own STRIPE_WEBHOOK_SECRET; this process signs with one ending "
                f"in sha256:{fingerprint(STATE.webhook_secret)}. Make sure both read the "
                "same value.",
                flush=True,
            )
        return code, body

    # -- API ---------------------------------------------------------------
    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        global STATE  # declared before first use, which Python requires

        path = urllib.parse.urlparse(self.path).path
        body = self._read()

        if path == "/v1/checkout/sessions":
            price_id = body.get("line_items[0][price]") or body.get("price") or ""
            try:
                session_id, url = STATE.create_checkout_session(
                    user_id=body.get("client_reference_id", ""),
                    email=body.get("customer_email", ""),
                    price_id=price_id,
                    customer_id=body.get("customer") or None,
                    success_url=body.get("success_url", ""),
                    cancel_url=body.get("cancel_url", ""),
                )
            except ValueError as error:
                return self._json(400, {"error": {"message": str(error)}})
            stored = STATE.sessions[session_id]
            return self._json(
                200,
                {
                    "id": session_id,
                    "object": "checkout.session",
                    "url": url,
                    "customer": stored["customer"],
                    "subscription": stored["subscription"],
                    "client_reference_id": stored["client_reference_id"],
                    "status": "open",
                },
            )

        if path == "/v1/billing_portal/sessions":
            try:
                url = STATE.portal_url(
                    customer_id=body.get("customer", ""), return_url=body.get("return_url", "")
                )
            except ValueError as error:
                return self._json(400, {"error": {"message": str(error)}})
            return self._json(200, {"id": "bps_fake", "url": url})

        # -- control surface (tests only) ----------------------------------
        if path == "/__control/pay":
            session_id = body.get("session_id", "")
            if session_id not in STATE.sessions:
                return self._json(404, {"error": {"message": "no such session"}})
            subscription_id = STATE.complete_checkout(session_id)
            delivered = [
                self._deliver(event)
                for event in (
                    STATE.checkout_completed_event(session_id),
                    STATE.subscription_event(subscription_id, "customer.subscription.created"),
                )
            ]
            return self._json(200, {"subscription": subscription_id, "delivered": delivered})

        if path == "/__control/cancel":
            subscription_id = body.get("subscription_id", "")
            if subscription_id not in STATE.subscriptions:
                return self._json(404, {"error": {"message": "no such subscription"}})
            STATE.set_subscription(subscription_id, status="canceled")
            delivered = [
                self._deliver(STATE.subscription_event(subscription_id, "customer.subscription.deleted"))
            ]
            return self._json(200, {"delivered": delivered})

        if path == "/__control/reset":
            STATE = new_state()
            return self._json(200, {"reset": True})

        return self._json(404, {"error": {"message": f"fake stripe has no {path}"}})

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path

        session = SESSION_RE.match(path)
        if session:
            stored = STATE.sessions.get(session.group(1))
            if stored is None:
                return self._json(404, {"error": {"message": "no such session"}})
            return self._json(200, stored)

        subscription = SUBSCRIPTION_RE.match(path)
        if subscription:
            resource = STATE.subscription_resource(subscription.group(1))
            if resource is None:
                return self._json(404, {"error": {"message": "no such subscription"}})
            return self._json(200, resource)

        return self._json(404, {"error": {"message": f"fake stripe has no {path}"}})

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"fake-stripe: {self.command} {self.path}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--webhook-target",
        default="",
        help="where to deliver signed events, e.g. http://127.0.0.1:8000/api/billing/webhook",
    )
    parser.add_argument(
        "--webhook-secret",
        default=default_webhook_secret(),
        help=(
            "secret to sign deliveries with; defaults to $STRIPE_WEBHOOK_SECRET, "
            "which is what the application verifies with"
        ),
    )
    parser.add_argument("--env", default="test", help="refuses to run as 'production'")
    args = parser.parse_args()

    if args.env == "production":
        print("refusing to run the fake payment provider against production", file=sys.stderr)
        return 2
    if args.port == 0:
        print("--port 0 is not supported: checkout urls would be unreachable", file=sys.stderr)
        return 2

    global PUBLIC_BASE, STATE, WEBHOOK_SECRET, WEBHOOK_TARGET
    WEBHOOK_TARGET = args.webhook_target or None
    WEBHOOK_SECRET = args.webhook_secret
    PUBLIC_BASE = f"http://{args.host if args.host != '0.0.0.0' else '127.0.0.1'}:{args.port}"
    STATE = new_state()

    print(f"fake stripe on http://{args.host}:{args.port}", flush=True)
    print(f"checkout urls will be {PUBLIC_BASE}/...", flush=True)
    source = "$STRIPE_WEBHOOK_SECRET" if os.environ.get("STRIPE_WEBHOOK_SECRET") else "the default"
    print(
        f"signing webhooks with the secret from {source} "
        f"(sha256:{fingerprint(WEBHOOK_SECRET)}, {len(WEBHOOK_SECRET)} chars)",
        flush=True,
    )
    if WEBHOOK_TARGET:
        print(f"delivering webhooks to {WEBHOOK_TARGET}", flush=True)
    else:
        print("no --webhook-target: webhooks will not be delivered", flush=True)

    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
