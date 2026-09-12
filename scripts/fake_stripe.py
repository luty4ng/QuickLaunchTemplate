#!/usr/bin/env python
"""A stand-in for Stripe, for end-to-end tests without an account.

The smoke test needs to prove the whole billing path works - checkout, payment,
a signed webhook, entitlement, and the quota lifting - against a **running
deployment**. Doing that with real Stripe would need credentials, a public
webhook URL and a human clicking a card form, so this serves the same HTTP
surface the app calls:

    POST /v1/checkout/sessions          create a checkout session
    POST /v1/billing_portal/sessions    create a portal session
    GET  /v1/subscriptions/{id}         retrieve a subscription
    POST /__control/pay                 test hook: mark a session paid
    POST /__control/reset               test hook: forget everything

It reuses `app.billing.fake.FakeGateway` for the state and for signing, so the
signatures it produces are the ones the application verifies, and the event
shapes are the ones the parsers expect. In other words, the only thing being
faked is Stripe's HTTP transport.

The control endpoints exist because a real payer is a human. They are prefixed
with `__control` and the script refuses to start with `--env production`.

    python scripts/fake_stripe.py --port 12194
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.billing.fake import FakeGateway  # noqa: E402

# Above Windows' reserved TCP range (12094-12193 on this machine, and similar on
# others): binding inside a reserved range fails with WinError 10013, which looks
# like "port in use" but is not.
DEFAULT_PORT = 12194

# The address this server actually listens on, set in main(). It has to be
# reachable rather than decorative: the smoke test derives the provider's control
# endpoint from the checkout url the application hands back, so a hardcoded
# placeholder host sends it to a name that does not resolve.
PUBLIC_BASE = f"http://127.0.0.1:{DEFAULT_PORT}"

GATEWAY = FakeGateway(base_url=PUBLIC_BASE)
WEBHOOK_TARGET: str | None = None

SESSION_RE = re.compile(r"^/v1/checkout/sessions/([^/]+)$")
SUBSCRIPTION_RE = re.compile(r"^/v1/subscriptions/([^/]+)$")


def _form(payload: bytes, content_type: str) -> dict[str, str]:
    """Stripe's API is form-encoded; flatten it into a dict of strings."""
    text = payload.decode() if payload else ""
    if "application/json" in content_type:
        return {k: str(v) for k, v in json.loads(text or "{}").items()}
    parsed = urllib.parse.parse_qs(text, keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items()}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # -- helpers -----------------------------------------------------------
    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length") or 0)
        return _form(self.rfile.read(length), self.headers.get("Content-Type", ""))

    def _deliver(self, event: dict) -> tuple[int, str]:
        """POST a signed event to the application's webhook, as Stripe would."""
        if not WEBHOOK_TARGET:
            return 0, "no webhook target configured"
        import urllib.error
        import urllib.request

        payload = json.dumps(event).encode()
        request = urllib.request.Request(
            WEBHOOK_TARGET,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Stripe-Signature": GATEWAY.sign(payload),
                "User-Agent": "fake-stripe",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                body = response.read().decode(errors="replace")
                print(
                    f"fake-stripe: delivered {event.get('type')} -> {response.status} {body[:200]}",
                    flush=True,
                )
                return response.status, body
        except urllib.error.HTTPError as error:
            body = error.read().decode(errors="replace")
            print(
                f"fake-stripe: delivered {event.get('type')} -> {error.code} {body[:300]}",
                flush=True,
            )
            return error.code, body
        except Exception as error:  # noqa: BLE001 - report, do not crash the fake
            print(f"fake-stripe: could not deliver {event.get('type')}: {error}", flush=True)
            return 0, str(error)

    # -- API ---------------------------------------------------------------
    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        # Declared before any use of the name, which Python requires.
        global GATEWAY

        path = urllib.parse.urlparse(self.path).path
        form = self._read()

        if path == "/v1/checkout/sessions":
            if not form.get("line_items[0][price]") and "line_items" not in form:
                return self._json(400, {"error": {"message": "no line items"}})

            # The app sends price and quantity per item; accept both shapes.
            price_id = form.get("line_items[0][price]") or form.get("price") or ""
            session = GATEWAY.create_checkout_session(
                user_id=form.get("client_reference_id", ""),
                email=form.get("customer_email", ""),
                price_id=price_id,
                customer_id=form.get("customer") or None,
                success_url=form.get("success_url", ""),
                cancel_url=form.get("cancel_url", ""),
            )
            stored = GATEWAY.sessions[session.session_id]
            return self._json(
                200,
                {
                    "id": session.session_id,
                    "object": "checkout.session",
                    "url": session.url,
                    "customer": stored["customer"],
                    "subscription": stored["subscription"],
                    "client_reference_id": stored["client_reference_id"],
                    "status": "open",
                },
            )

        if path == "/v1/billing_portal/sessions":
            try:
                url = GATEWAY.create_portal_session(
                    customer_id=form.get("customer", ""), return_url=form.get("return_url", "")
                )
            except Exception as error:  # noqa: BLE001
                return self._json(400, {"error": {"message": str(error)}})
            return self._json(200, {"id": "bps_fake", "url": url})

        match = SESSION_RE.match(path)
        if match:
            session = GATEWAY.sessions.get(match.group(1))
            if session is None:
                return self._json(404, {"error": {"message": "no such session"}})
            return self._json(200, session)

        # -- control surface (tests only) ----------------------------------
        if path == "/__control/pay":
            session_id = form.get("session_id", "")
            if session_id not in GATEWAY.sessions:
                return self._json(404, {"error": {"message": "no such session"}})
            subscription_id = GATEWAY.complete_checkout(session_id)
            delivered = []
            for event in (
                GATEWAY.checkout_completed_event(session_id),
                GATEWAY.subscription_event(subscription_id, "customer.subscription.created"),
            ):
                delivered.append(self._deliver(event))
            return self._json(200, {"subscription": subscription_id, "delivered": delivered})

        if path == "/__control/cancel":
            subscription_id = form.get("subscription_id", "")
            if subscription_id not in GATEWAY.subscriptions:
                return self._json(404, {"error": {"message": "no such subscription"}})
            GATEWAY.set_subscription(subscription_id, status="canceled")
            status, body = self._deliver(
                GATEWAY.subscription_event(subscription_id, "customer.subscription.deleted")
            )
            return self._json(200, {"delivered": [[status, body]]})

        if path == "/__control/reset":
            GATEWAY = FakeGateway(base_url=PUBLIC_BASE)
            return self._json(200, {"reset": True})

        return self._json(404, {"error": {"message": f"fake stripe has no {path}"}})

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path

        subscription = SUBSCRIPTION_RE.match(path)
        if subscription:
            snapshot = GATEWAY.subscriptions.get(subscription.group(1))
            if snapshot is None:
                return self._json(404, {"error": {"message": "no such subscription"}})
            return self._json(
                200,
                {
                    "id": snapshot.subscription_id,
                    "object": "subscription",
                    "customer": snapshot.customer_id,
                    "status": snapshot.status,
                    "cancel_at_period_end": snapshot.cancel_at_period_end,
                    "metadata": {"user_id": snapshot.user_id},
                    # The period end lives on the item, as in recent API versions.
                    "items": {
                        "object": "list",
                        "data": [
                            {
                                "id": f"si_{snapshot.subscription_id}",
                                "object": "subscription_item",
                                "price": {"id": snapshot.price_id},
                                "current_period_end": int(snapshot.current_period_end.timestamp())
                                if snapshot.current_period_end
                                else None,
                            }
                        ],
                    },
                },
            )

        return self._json(404, {"error": {"message": f"fake stripe has no {path}"}})

    def log_message(self, fmt: str, *args: object) -> None:
        # One line per request so a CI log shows what the app actually asked for.
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
    parser.add_argument("--env", default="test", help="refuses to run as 'production'")
    args = parser.parse_args()

    if args.env == "production":
        print("refusing to run the fake payment provider against production", file=sys.stderr)
        return 2

    global GATEWAY, PUBLIC_BASE, WEBHOOK_TARGET
    WEBHOOK_TARGET = args.webhook_target or None

    # Checkout urls must point back here, because the smoke test uses them to
    # find these control endpoints. A port of 0 would not be knowable in advance,
    # so it is rejected rather than silently producing unreachable urls.
    if args.port == 0:
        print("--port 0 is not supported: checkout urls would be unreachable", file=sys.stderr)
        return 2
    PUBLIC_BASE = f"http://{args.host}:{args.port}"
    GATEWAY = FakeGateway(base_url=PUBLIC_BASE)

    print(f"fake stripe on {PUBLIC_BASE}", flush=True)
    if WEBHOOK_TARGET:
        print(f"delivering webhooks to {WEBHOOK_TARGET}", flush=True)
    else:
        print("no --webhook-target: webhooks will not be delivered", flush=True)

    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
