"""The payment provider boundary.

Everything that talks to Stripe goes through this interface, for three reasons:

1. **The test suite stays offline.** The 45 existing tests never touch the
   network; adding payments must not change that. Tests inject `FakeGateway`.
2. **No accidental live calls.** Without credentials the real gateway refuses to
   build, and the routes answer 503 instead of pretending to work.
3. **Stripe's shapes stay in one file.** Its API changes between versions
   (`current_period_end` moved from the subscription to its items in recent API
   versions), so the parsing lives here and produces one `SubscriptionSnapshot`
   that the rest of the app trusts.

Nothing here decides what a user is allowed to do - that is the service layer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from app.features.billing.settings import BillingSettings


class BillingUnavailable(RuntimeError):
    """Stripe is not configured, so the request cannot be served."""


@dataclass(frozen=True)
class SubscriptionSnapshot:
    """What the app needs to know about a subscription, provider-agnostic."""

    subscription_id: str
    customer_id: str
    status: str
    price_id: str
    # Absolute end of the paid period. None when Stripe did not report one.
    current_period_end: datetime | None
    cancel_at_period_end: bool
    # Our own user id, carried in the subscription's metadata. Set by us at
    # checkout time and echoed back by Stripe, which is how a webhook finds its
    # user before any customer id has been stored.
    user_id: str = ""

    @property
    def is_active(self) -> bool:
        """Statuses that entitle the user to the paid plan.

        `past_due` is included on purpose: Stripe retries a failed renewal for
        days, and cutting someone off the moment a card expires is worse than
        letting the retry finish. `unpaid`, `canceled` and `incomplete*` are not.
        """
        return self.status in {"active", "trialing", "past_due"}


@dataclass(frozen=True)
class CheckoutSession:
    url: str
    session_id: str


class BillingGateway(Protocol):
    """The provider operations the application performs."""

    def create_checkout_session(
        self,
        *,
        user_id: str,
        email: str,
        price_id: str,
        customer_id: str | None,
        success_url: str,
        cancel_url: str,
    ) -> CheckoutSession: ...

    def create_portal_session(self, *, customer_id: str, return_url: str) -> str: ...

    def get_subscription(self, subscription_id: str) -> SubscriptionSnapshot | None: ...

    def parse_webhook(self, payload: bytes, signature: str) -> dict[str, Any]: ...


def _as_datetime(value: Any) -> datetime | None:
    """Normalise whatever a provider sent for a timestamp.

    Stripe's REST payloads use unix seconds, but the SDK's objects can hand back
    an ISO string depending on the field and version, and a datetime can appear
    when a value has been through pydantic. Accepting all three avoids a silent
    `None` that would show up as a missing renewal date in the UI.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def snapshot_from_stripe(subscription: Any, price_id: str = "") -> SubscriptionSnapshot:
    """Normalise a Stripe subscription object.

    `current_period_end` has moved between the subscription and its items across
    API versions. Rather than trusting either location, take whichever reports a
    value - the API version is pinned by the SDK, but this survives the next
    bump without a silent `None`.
    """
    items = _get(subscription, "items")
    item_data = _get(items, "data") or []
    first_item = item_data[0] if item_data else None

    resolved_price = price_id or nested_price_id(first_item)
    period_end = _as_datetime(_get(subscription, "current_period_end"))
    if period_end is None:
        period_end = _as_datetime(_get(first_item, "current_period_end"))
    if period_end is None:
        period_end = _as_datetime(nested_period_end(first_item))

    return SubscriptionSnapshot(
        subscription_id=str(_get(subscription, "id") or ""),
        customer_id=str(_get(subscription, "customer") or ""),
        status=str(_get(subscription, "status") or ""),
        price_id=resolved_price,
        current_period_end=period_end,
        cancel_at_period_end=bool(_get(subscription, "cancel_at_period_end")),
        user_id=str(_get(_get(subscription, "metadata"), "user_id") or ""),
    )


def _get(obj: Any, key: str) -> Any:
    """Read a key from a StripeObject or a plain dict; None when absent."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def nested_period_end(item: Any) -> Any:
    """`current_period_end` inside an item, which itself may be nested."""
    direct = _get(item, "current_period_end")
    if direct is not None:
        return direct
    return _get(_get(item, "plan"), "current_period_end")


def nested_price_id(item: Any) -> str:
    if item is None:
        return ""
    price = _get(item, "price")
    price_id = _get(price, "id")
    if price_id:
        return str(price_id)
    plan_id = _get(_get(item, "plan"), "id")
    return str(plan_id or "")


def verify_signature(payload: bytes, signature: str, secret: str, tolerance: int) -> dict[str, Any]:
    """Verify a `Stripe-Signature` header and return the parsed event.

    Shared by every gateway so there is exactly one implementation of the check
    that decides whether a payment happened. Raises ValueError when the signature
    is wrong or the timestamp is outside the tolerance.

    The timestamp is checked in **both** directions here, ahead of the SDK's own
    check. Measured against stripe-python 15.6.1: `construct_event` accepts a
    header dated an hour in the FUTURE and rejects one an hour in the past. A
    captured request could therefore be replayed indefinitely by editing `t`
    upward, because the timestamp is part of what is signed. A future-dated
    header is a replay.
    """
    _reject_future_timestamp(signature, tolerance)

    import stripe

    try:
        event = stripe.Webhook.construct_event(payload, signature, secret, tolerance=tolerance)
    except Exception as exc:  # noqa: BLE001 - see below
        # The SDK raises `SignatureVerificationError`, which - unlike the fake
        # gateway's `SignatureError` - does NOT derive from ValueError, so an
        # uncaught one reached the caller as a 500. That is not just cosmetic:
        # Stripe retries a 500 forever, and a 500 says "our fault, try again"
        # about what is really a rejected request. Anything the SDK refuses to
        # verify is a bad request, so it is translated here, once, for both
        # gateways.
        raise ValueError(f"{type(exc).__name__}: {exc}") from exc
    return normalise_event(event)


def _reject_future_timestamp(signature: str, tolerance: int) -> None:
    timestamp = None
    for part in (signature or "").split(","):
        key, _, value = part.partition("=")
        if key.strip() == "t" and value.strip().isdigit():
            timestamp = int(value)
            break
    if timestamp is None:
        # No usable timestamp: let the SDK decide, so its error is the one the
        # caller sees rather than a guess of ours.
        return
    ahead = timestamp - int(time.time())
    if ahead > tolerance:
        raise ValueError(f"timestamp is {ahead}s in the future, beyond the {tolerance}s tolerance")


class StripeGateway:
    """The real thing. Constructed only when settings carry usable credentials."""

    def __init__(self, settings: BillingSettings) -> None:
        if not settings.stripe_secret_key:
            raise BillingUnavailable("STRIPE_SECRET_KEY is not configured")
        if not settings.stripe_webhook_secret:
            # The webhook is the only thing that grants a plan, so a missing
            # signing secret is a misconfiguration worth refusing outright.
            raise BillingUnavailable("STRIPE_WEBHOOK_SECRET is not configured")

        import stripe  # imported lazily so the module loads without the SDK

        self._stripe = stripe
        self._settings = settings
        # The SDK pins the API version it was generated against; setting the key
        # per request keeps this process from mutating global state.
        self._api_key = settings.stripe_secret_key

    def _call(self, method: Any, /, **kwargs: Any) -> Any:
        return method(**kwargs, api_key=self._api_key)

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
        params: dict[str, Any] = {
            "mode": "subscription",
            "line_items": [{"price": price_id, "quantity": 1}],
            "success_url": success_url,
            "cancel_url": cancel_url,
            # client_reference_id is how the webhook finds our user even before
            # any customer id is stored. It is set by us, never by the browser.
            "client_reference_id": user_id,
            "subscription_data": {"metadata": {"user_id": user_id}},
        }
        if customer_id:
            params["customer"] = customer_id
        else:
            params["customer_email"] = email

        session = self._call(self._stripe.checkout.Session.create, **params)
        url = getattr(session, "url", None)
        if not url:
            raise BillingUnavailable("Stripe did not return a checkout url")
        return CheckoutSession(url=url, session_id=str(getattr(session, "id", "")))

    def create_portal_session(self, *, customer_id: str, return_url: str) -> str:
        session = self._call(
            self._stripe.billing_portal.Session.create,
            customer=customer_id,
            return_url=return_url,
        )
        url = getattr(session, "url", None)
        if not url:
            raise BillingUnavailable("Stripe did not return a portal url")
        return str(url)

    def get_subscription(self, subscription_id: str) -> SubscriptionSnapshot | None:
        try:
            subscription = self._call(self._stripe.Subscription.retrieve, id=subscription_id)
        except Exception:  # noqa: BLE001 - a missing subscription is not an error here
            return None
        return snapshot_from_stripe(subscription)

    def parse_webhook(self, payload: bytes, signature: str) -> dict[str, Any]:
        """Verify the signature and return a normalised event.

        Delegates to `verify_signature` so the SDK path and the HTTP path cannot
        drift apart in what they accept.
        """
        return verify_signature(
            payload,
            signature,
            self._settings.stripe_webhook_secret,
            self._settings.stripe_webhook_tolerance,
        )


def normalise_event(event: Any) -> dict[str, Any]:
    """Reduce a Stripe event (or anything shaped like one) to three fields.

    Tolerates both the dict the fake gateway returns and the SDK's StripeObject,
    and both `data.object` and the flatter shape newer API versions use.
    """
    data = _get(event, "data")
    obj = _get(data, "object")
    if obj is None and isinstance(data, dict):
        # Some API versions drop the "object" wrapper and put fields directly
        # under data; fall back to a shallow copy so lookups below still work.
        obj = {k: v for k, v in data.items() if k != "object"}

    return {
        "id": str(_get(event, "id") or ""),
        "type": str(_get(event, "type") or ""),
        "object": obj if isinstance(obj, dict) else _object_to_dict(obj),
    }


def _object_to_dict(obj: Any) -> dict[str, Any]:
    """Convert a StripeObject into a plain dict.

    `dict(stripeObject)` raises: Stripe's objects are not mappings (checked
    against stripe-python 15.6.1). `to_dict()` is the supported conversion, and
    converting matters because the service reads the event object with dict
    access.
    """
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    for name in ("to_dict", "to_dict_recursive"):
        convert = getattr(obj, name, None)
        if callable(convert):
            converted = convert()
            if isinstance(converted, dict):
                return converted
    return {}
