"""An in-memory stand-in for Stripe, as a `BillingGateway`.

The state and event building live in `scripts/fake_stripe_core.py`, which imports
only the standard library. That is not tidiness for its own sake: the deployment
smoke test starts the fake provider with a bare `python3`, so anything importing
the application (and therefore pydantic) cannot be used there. Sharing one core
also means the gateway the tests drive and the HTTP server CI drives cannot
disagree about event shapes - which is exactly the bug that hid an entire broken
webhook path.

`scripts/fake_stripe.py` wraps the same core in an HTTP server, so the smoke test
exercises the real transport rather than a shortcut.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# The core lives with the scripts; make it importable from the app.
_SCRIPTS = Path(__file__).resolve().parents[4] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from fake_stripe_core import FakeStripeState, SignatureError  # noqa: E402

from app.features.billing.gateway import (  # noqa: E402
    BillingUnavailable,
    CheckoutSession,
    SubscriptionSnapshot,
    normalise_event,
)

__all__ = ["FakeGateway", "FakeStripeState", "SignatureError"]


class FakeGateway:
    """The provider interface backed by `FakeStripeState`."""

    def __init__(self, **kwargs: Any) -> None:
        # `now` is the old keyword the tests used for a deterministic clock.
        clock = kwargs.pop("now", None)
        self.state = FakeStripeState(**kwargs)
        if clock is not None:
            self.state.clock = clock

    # -- convenience passthroughs used by tests ----------------------------
    @property
    def sessions(self) -> dict[str, dict[str, Any]]:
        return self.state.sessions

    @property
    def subscriptions(self) -> dict[str, Any]:
        return self.state.subscriptions

    @property
    def checkout_calls(self) -> list[dict[str, Any]]:
        return self.state.checkout_calls

    @property
    def portal_calls(self) -> list[dict[str, Any]]:
        return self.state.portal_calls

    @property
    def webhook_secret(self) -> str:
        return self.state.webhook_secret

    @property
    def tolerance(self) -> int:
        return self.state.tolerance

    def now(self) -> float:
        return float(self.state.clock())

    def sign(self, payload: bytes, timestamp: int | None = None) -> str:
        return self.state.sign(payload, timestamp)

    def complete_checkout(self, session_id: str, price_id: str | None = None) -> str:
        return self.state.complete_checkout(session_id, price_id)

    def set_subscription(self, subscription_id: str, **changes: Any):
        return self.state.set_subscription(subscription_id, **changes)

    def checkout_completed_event(self, session_id: str) -> dict[str, Any]:
        return self.state.checkout_completed_event(session_id)

    def subscription_event(self, subscription_id: str, event_type: str) -> dict[str, Any]:
        return self.state.subscription_event(subscription_id, event_type)

    def invoice_paid_event(self, subscription_id: str) -> dict[str, Any]:
        return self.state.invoice_paid_event(subscription_id)

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
        try:
            session_id, url = self.state.create_checkout_session(
                user_id=user_id,
                email=email,
                price_id=price_id,
                customer_id=customer_id,
                success_url=success_url,
                cancel_url=cancel_url,
            )
        except ValueError as exc:
            raise BillingUnavailable(str(exc)) from exc
        return CheckoutSession(url=url, session_id=session_id)

    def create_portal_session(self, *, customer_id: str, return_url: str) -> str:
        try:
            return self.state.portal_url(customer_id=customer_id, return_url=return_url)
        except ValueError as exc:
            raise BillingUnavailable(str(exc)) from exc

    def get_subscription(self, subscription_id: str) -> SubscriptionSnapshot | None:
        subscription = self.state.subscriptions.get(subscription_id)
        if subscription is None:
            return None
        return SubscriptionSnapshot(
            subscription_id=subscription.subscription_id,
            customer_id=subscription.customer_id,
            status=subscription.status,
            price_id=subscription.price_id,
            current_period_end=subscription.current_period_end,
            cancel_at_period_end=subscription.cancel_at_period_end,
            user_id=subscription.user_id,
        )

    def parse_webhook(self, payload: bytes, signature: str) -> dict[str, Any]:
        """Verify and normalise, so the shape matches every other gateway."""
        return normalise_event(self.state.verify(payload, signature))
