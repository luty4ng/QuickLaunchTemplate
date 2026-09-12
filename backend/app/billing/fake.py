"""An in-memory stand-in for Stripe.

Tests must not depend on a network, a Stripe account or a clock, so this gateway
implements the same interface and produces the same event shape the real one
does. It signs and verifies webhooks with the same HMAC scheme Stripe uses, which
means the signature test exercises real verification rather than a mock that
always says yes.

It is also what `scripts/fake_stripe.py` wraps in an HTTP server, so the CI smoke
test can drive the whole flow - checkout, payment, webhook, entitlement - with no
credentials at all.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any

from app.billing.gateway import (
    BillingUnavailable,
    CheckoutSession,
    SubscriptionSnapshot,
    _as_datetime,
    normalise_event,
)

DEFAULT_TOLERANCE = 300


class SignatureError(ValueError):
    """The signature did not verify, or the timestamp was too far off."""


@dataclass
class FakeGateway:
    """Stateful enough to imitate a subscription's life cycle."""

    # Injected so tests can be deterministic; production never uses this class.
    now: callable = time.time
    webhook_secret: str = "whsec_fake_secret"
    tolerance: int = DEFAULT_TOLERANCE
    base_url: str = "https://checkout.example.test"

    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    subscriptions: dict[str, SubscriptionSnapshot] = field(default_factory=dict)
    checkout_calls: list[dict[str, Any]] = field(default_factory=list)
    portal_calls: list[dict[str, Any]] = field(default_factory=list)
    _counter: int = 0

    # -- helpers -----------------------------------------------------------
    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}_fake{self._counter:04d}"

    # -- BillingGateway ----------------------------------------------------
    def create_checkout_session(
        self,
        *,
        user_id: str,
        email: str,
        price_id: str,
        customer_id: str | None,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSession:
        if not price_id:
            raise BillingUnavailable("no price configured for that plan")

        session_id = self._next_id("cs_test")
        customer = customer_id or self._next_id("cus_test")
        subscription_id = self._next_id("sub_test")
        # Mirrors what the application actually sends (and what Stripe echoes
        # back): client_reference_id, plus the user id in metadata - both are
        # places the webhook handler looks, and a fake that omits one would hide
        # a real resolver bug.
        metadata = {"user_id": user_id}
        self.sessions[session_id] = {
            "id": session_id,
            "customer": customer,
            "customer_email": email,
            "subscription": subscription_id,
            "client_reference_id": user_id,
            "metadata": metadata,
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
        return CheckoutSession(url=f"{self.base_url}/{session_id}", session_id=session_id)

    def create_portal_session(self, *, customer_id: str, return_url: str) -> str:
        if not customer_id:
            raise BillingUnavailable("no customer to open a portal for")
        self.portal_calls.append({"customer_id": customer_id, "return_url": return_url})
        return f"{self.base_url}/portal/{customer_id}"

    def get_subscription(self, subscription_id: str) -> SubscriptionSnapshot | None:
        return self.subscriptions.get(subscription_id)

    def parse_webhook(self, payload: bytes, signature: str) -> dict[str, Any]:
        """Verify and normalise, through the same code the real gateways use.

        The HMAC is computed here (there is no SDK and no network), but the
        *outcome* goes through the shared `verify_signature` verification and
        `normalise_event` unwrapping. An earlier version returned the raw event
        dict instead, which meant the test double produced a different shape from
        production - and that divergence is exactly what hid a bug where the
        service saw an empty object for every event.
        """
        timestamp, expected = self._split_signature(signature)
        if timestamp is None:
            raise SignatureError("malformed Stripe-Signature header")

        signed = f"{timestamp}.".encode() + payload
        computed = hmac.new(self.webhook_secret.encode(), signed, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(computed, expected):
            raise SignatureError("signature does not match")

        age = abs(self.now() - int(timestamp))
        if age > self.tolerance:
            raise SignatureError(f"timestamp outside tolerance ({age:.0f}s)")

        return normalise_event(json.loads(payload))

    @staticmethod
    def _split_signature(header: str) -> tuple[str | None, str]:
        timestamp = None
        signature = ""
        for part in header.split(","):
            key, _, value = part.partition("=")
            if key == "t":
                timestamp = value
            elif key == "v1":
                signature = value
        return timestamp, signature

    def sign(self, payload: bytes, timestamp: int | None = None) -> str:
        """Produce a valid Stripe-Signature header, the way `stripe listen` does."""
        stamp = int(timestamp if timestamp is not None else self.now())
        signed = f"{stamp}.".encode() + payload
        digest = hmac.new(self.webhook_secret.encode(), signed, hashlib.sha256).hexdigest()
        return f"t={stamp},v1={digest}"

    # -- test conveniences -------------------------------------------------
    def complete_checkout(self, session_id: str, *, price_id: str | None = None) -> str:
        """Mark a session paid and create the subscription it implies.

        Returns the subscription id, so a test can then emit the webhooks Stripe
        would have sent.
        """
        session = self.sessions[session_id]
        session["status"] = "complete"
        price = price_id or session["price_id"]
        snapshot = SubscriptionSnapshot(
            subscription_id=session["subscription"],
            customer_id=session["customer"],
            status="active",
            price_id=price,
            current_period_end=_as_datetime(int(self.now()) + 30 * 24 * 3600),
            cancel_at_period_end=False,
            user_id=session["client_reference_id"],
        )
        self.subscriptions[snapshot.subscription_id] = snapshot
        return snapshot.subscription_id

    def set_subscription(self, subscription_id: str, **changes: Any) -> SubscriptionSnapshot:
        current = self.subscriptions[subscription_id]
        updated = SubscriptionSnapshot(
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

    # -- event builders ----------------------------------------------------
    #
    # All of these wrap the payload in `data.object`, which is the shape Stripe
    # actually sends. Emitting the object at the top level instead (as an earlier
    # version did) made the parser return empty dicts and every webhook came
    # back "unmatched" - the fake has to be wrong in the same ways as nothing,
    # not in new ways of its own.

    def checkout_completed_event(self, session_id: str) -> dict[str, Any]:
        session = self.sessions[session_id]
        return {
            "id": self._next_id("evt"),
            "type": "checkout.session.completed",
            "data": {"object": dict(session)},
        }

    def subscription_event(self, subscription_id: str, event_type: str) -> dict[str, Any]:
        snapshot = self.subscriptions[subscription_id]
        price = {"id": snapshot.price_id}
        # Mirrors the real payload: the period end lives on the item in recent
        # API versions, which is why the parser checks both places.
        item = {
            "id": f"si_{snapshot.subscription_id}",
            "price": price,
            "current_period_end": int(snapshot.current_period_end.timestamp())
            if snapshot.current_period_end
            else None,
        }
        # Real subscriptions carry the user id we set at checkout time
        # (subscription_data.metadata). Omitting it here made a test fail for a
        # reason that could not happen in production, so the fake has to send it.
        metadata = {"user_id": snapshot.user_id} if getattr(snapshot, "user_id", "") else {}
        return {
            "id": self._next_id("evt"),
            "type": event_type,
            "data": {
                "object": {
                    "id": snapshot.subscription_id,
                    "customer": snapshot.customer_id,
                    "status": snapshot.status,
                    "cancel_at_period_end": snapshot.cancel_at_period_end,
                    "metadata": metadata,
                    "items": {"data": [item]},
                }
            },
        }

    def invoice_paid_event(self, subscription_id: str) -> dict[str, Any]:
        snapshot = self.subscriptions[subscription_id]
        return {
            "id": self._next_id("evt"),
            "type": "invoice.paid",
            "data": {
                "object": {
                    "id": self._next_id("in_test"),
                    "customer": snapshot.customer_id,
                    "subscription": snapshot.subscription_id,
                    "amount_paid": 1200,
                    "currency": "usd",
                }
            },
        }
