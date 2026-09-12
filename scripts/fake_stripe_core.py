"""The fake payment provider's logic, with no application dependencies.

Split out from `scripts/fake_stripe.py` for a practical reason: that script runs
on a bare interpreter (the deployment smoke test starts it with system `python3`,
without the backend's dependencies installed), so it cannot import the app
package. This module therefore imports nothing but the standard library, while
`app/billing/fake.py` re-exports it for the test suite.

It lives under scripts/ rather than in the app because it is a test tool; the app
only ever sees it through the `BillingGateway` interface.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


class SignatureError(ValueError):
    """The signature did not verify, or the timestamp was too far off."""


# The secret the fake signs with when nothing else is configured. Real runs take
# `STRIPE_WEBHOOK_SECRET` from the environment - the same variable the
# application verifies with - so the two sides cannot be given different values
# by accident. They were, once: CI signed with this default while the container
# verified the value from the workflow's `env:` block, and every webhook came
# back 500 "No signatures found matching the expected signature for payload".
DEFAULT_WEBHOOK_SECRET = "whsec_fake_secret"


def default_webhook_secret() -> str:
    """The signing secret, following the application's own configuration.

    A factory rather than a constant so the environment is read when a state
    object is built, not when this module is imported.
    """
    return os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip() or DEFAULT_WEBHOOK_SECRET


def as_datetime(value: Any) -> datetime | None:
    """Accept unix seconds, an ISO string, or a datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


@dataclass(frozen=True)
class Subscription:
    """A subscription, in the same shape the real gateway produces."""

    subscription_id: str
    customer_id: str
    status: str
    price_id: str
    current_period_end: datetime | None
    cancel_at_period_end: bool
    user_id: str = ""


@dataclass
class FakeStripeState:
    """In-memory provider state, plus event building and signing.

    Deliberately keeps the same event shape Stripe uses (`data.object`): an
    earlier version put the payload at the top level, and the mismatch made every
    webhook arrive looking empty.
    """

    webhook_secret: str = field(default_factory=default_webhook_secret)
    tolerance: int = 300
    base_url: str = "http://127.0.0.1:12194"
    clock: Any = time.time

    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    subscriptions: dict[str, Subscription] = field(default_factory=dict)
    checkout_calls: list[dict[str, Any]] = field(default_factory=list)
    portal_calls: list[dict[str, Any]] = field(default_factory=list)
    _counter: int = 0

    # -- ids ---------------------------------------------------------------
    def next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}_fake{self._counter:04d}"

    # -- provider operations ----------------------------------------------
    def create_checkout_session(
        self,
        *,
        user_id: str,
        email: str,
        price_id: str,
        customer_id: str | None,
        success_url: str,
        cancel_url: str,
    ) -> tuple[str, str]:
        """Returns (session_id, url)."""
        if not price_id:
            raise ValueError("no price configured for that plan")

        session_id = self.next_id("cs_test")
        customer = customer_id or self.next_id("cus_test")
        subscription_id = self.next_id("sub_test")
        self.sessions[session_id] = {
            "id": session_id,
            "customer": customer,
            "customer_email": email,
            "subscription": subscription_id,
            "client_reference_id": user_id,
            "metadata": {"user_id": user_id},
            "price_id": price_id,
            "status": "open",
        }
        self.checkout_calls.append(
            {
                "user_id": user_id,
                "email": email,
                "price_id": price_id,
                "customer_id": customer_id,
                "success_url": success_url,
                "cancel_url": cancel_url,
                "session_id": session_id,
            }
        )
        return session_id, f"{self.base_url}/{session_id}"

    def portal_url(self, *, customer_id: str, return_url: str) -> str:
        if not customer_id:
            raise ValueError("no customer to open a portal for")
        self.portal_calls.append({"customer_id": customer_id, "return_url": return_url})
        return f"{self.base_url}/portal/{customer_id}"

    def complete_checkout(self, session_id: str, price_id: str | None = None) -> str:
        """Mark a session paid and create the subscription it implies."""
        session = self.sessions[session_id]
        session["status"] = "complete"
        price = price_id or session["price_id"]
        subscription = Subscription(
            subscription_id=session["subscription"],
            customer_id=session["customer"],
            status="active",
            price_id=price,
            current_period_end=as_datetime(int(self.clock()) + 30 * 24 * 3600),
            cancel_at_period_end=False,
            user_id=session["client_reference_id"],
        )
        self.subscriptions[subscription.subscription_id] = subscription
        return subscription.subscription_id

    def set_subscription(self, subscription_id: str, **changes: Any) -> Subscription:
        current = self.subscriptions[subscription_id]
        updated = Subscription(
            subscription_id=current.subscription_id,
            customer_id=changes.get("customer_id", current.customer_id),
            status=changes.get("status", current.status),
            price_id=changes.get("price_id", current.price_id),
            current_period_end=changes.get("current_period_end", current.current_period_end),
            cancel_at_period_end=changes.get("cancel_at_period_end", current.cancel_at_period_end),
            user_id=current.user_id,
        )
        self.subscriptions[subscription_id] = updated
        return updated

    # -- events ------------------------------------------------------------
    def checkout_completed_event(self, session_id: str) -> dict[str, Any]:
        return {
            "id": self.next_id("evt"),
            "type": "checkout.session.completed",
            "data": {"object": dict(self.sessions[session_id])},
        }

    def subscription_event(self, subscription_id: str, event_type: str) -> dict[str, Any]:
        snapshot = self.subscriptions[subscription_id]
        item = {
            "id": f"si_{snapshot.subscription_id}",
            "price": {"id": snapshot.price_id},
            # The period end lives on the item in recent API versions.
            "current_period_end": int(snapshot.current_period_end.timestamp())
            if snapshot.current_period_end
            else None,
        }
        return {
            "id": self.next_id("evt"),
            "type": event_type,
            "data": {
                "object": {
                    "id": snapshot.subscription_id,
                    "customer": snapshot.customer_id,
                    "status": snapshot.status,
                    "cancel_at_period_end": snapshot.cancel_at_period_end,
                    "metadata": {"user_id": snapshot.user_id} if snapshot.user_id else {},
                    "items": {"data": [item]},
                }
            },
        }

    def invoice_paid_event(self, subscription_id: str) -> dict[str, Any]:
        snapshot = self.subscriptions[subscription_id]
        return {
            "id": self.next_id("evt"),
            "type": "invoice.paid",
            "data": {
                "object": {
                    "id": self.next_id("in_test"),
                    "customer": snapshot.customer_id,
                    "subscription": snapshot.subscription_id,
                    "amount_paid": 1200,
                    "currency": "usd",
                }
            },
        }

    def subscription_resource(self, subscription_id: str) -> dict[str, Any] | None:
        """The GET /v1/subscriptions/{id} representation."""
        snapshot = self.subscriptions.get(subscription_id)
        if snapshot is None:
            return None
        return {
            "id": snapshot.subscription_id,
            "object": "subscription",
            "customer": snapshot.customer_id,
            "status": snapshot.status,
            "cancel_at_period_end": snapshot.cancel_at_period_end,
            "metadata": {"user_id": snapshot.user_id} if snapshot.user_id else {},
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
        }

    # -- signing -----------------------------------------------------------
    def sign(self, payload: bytes, timestamp: int | None = None) -> str:
        stamp = int(timestamp if timestamp is not None else self.clock())
        digest = hmac.new(
            self.webhook_secret.encode(), f"{stamp}.".encode() + payload, hashlib.sha256
        ).hexdigest()
        return f"t={stamp},v1={digest}"

    def verify(self, payload: bytes, signature: str) -> dict[str, Any]:
        """Verify the way Stripe does, and raise SIGNATURE errors loudly."""
        timestamp = None
        expected = ""
        for part in (signature or "").split(","):
            key, _, value = part.partition("=")
            if key == "t":
                timestamp = value
            elif key == "v1":
                expected = value
        if timestamp is None:
            raise SignatureError("malformed Stripe-Signature header")

        digest = hmac.new(
            self.webhook_secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(digest, expected):
            raise SignatureError("signature does not match")

        age = abs(self.clock() - int(timestamp))
        if age > self.tolerance:
            raise SignatureError(f"timestamp outside tolerance ({age:.0f}s)")
        return json.loads(payload)
