"""A Stripe-compatible client that speaks plain HTTP to a configurable host.

Only used when `STRIPE_API_BASE` points somewhere other than api.stripe.com - in
practice at `scripts/fake_stripe.py`, which lets CI exercise the **whole** billing
path (checkout over HTTP, payment, a signed webhook, entitlement, the quota
lifting) without a Stripe account, credentials or a human with a card.

It is not a second implementation of Stripe's API for its own sake: the
alternative was to have CI skip billing entirely, and an untested payment path is
worse than a small adapter.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from app.billing.gateway import (
    BillingUnavailable,
    CheckoutSession,
    SubscriptionSnapshot,
    normalise_event,
    snapshot_from_stripe,
    verify_signature,
)
from app.config import Settings


class HttpStripeGateway:
    """Same interface as StripeGateway, over HTTP instead of the SDK."""

    def __init__(self, settings: Settings) -> None:
        if not settings.stripe_webhook_secret:
            raise BillingUnavailable("STRIPE_WEBHOOK_SECRET is not configured")
        self._base = settings.stripe_api_base.rstrip("/")
        self._key = settings.stripe_secret_key
        self._settings = settings

    # -- transport ---------------------------------------------------------
    def _request(self, method: str, path: str, data: dict[str, str] | None = None) -> dict[str, Any]:
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        request = urllib.request.Request(f"{self._base}{path}", data=body, method=method)
        request.add_header("Authorization", f"Bearer {self._key}")
        request.add_header("User-Agent", "quicklaunch-billing")
        if body:
            request.add_header("Content-Type", "application/x-www-form-urlencoded")

        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")
            raise BillingUnavailable(f"{method} {path} -> {error.code}: {detail[:200]}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise BillingUnavailable(f"{method} {path} failed: {error}") from error

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
        form = {
            "mode": "subscription",
            "line_items[0][price]": price_id,
            "line_items[0][quantity]": "1",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": user_id,
        }
        if customer_id:
            form["customer"] = customer_id
        else:
            form["customer_email"] = email

        payload = self._request("POST", "/v1/checkout/sessions", form)
        url = payload.get("url")
        if not url:
            raise BillingUnavailable("the provider did not return a checkout url")
        return CheckoutSession(url=str(url), session_id=str(payload.get("id") or ""))

    def create_portal_session(self, *, customer_id: str, return_url: str) -> str:
        payload = self._request(
            "POST",
            "/v1/billing_portal/sessions",
            {"customer": customer_id, "return_url": return_url},
        )
        url = payload.get("url")
        if not url:
            raise BillingUnavailable("the provider did not return a portal url")
        return str(url)

    def get_subscription(self, subscription_id: str) -> SubscriptionSnapshot | None:
        try:
            payload = self._request("GET", f"/v1/subscriptions/{subscription_id}")
        except BillingUnavailable:
            return None
        return snapshot_from_stripe(payload)

    def parse_webhook(self, payload: bytes, signature: str) -> dict[str, Any]:
        """Verify exactly as the SDK path does, future-dated headers included.

        Sharing `verify_signature` is the point: a test must not be able to pass
        against a weaker check than the deployment runs.
        """
        return verify_signature(
            payload,
            signature,
            self._settings.stripe_webhook_secret,
            self._settings.stripe_webhook_tolerance,
        )


__all__ = ["HttpStripeGateway", "normalise_event"]
