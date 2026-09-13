"""Turning verified provider events into entitlements.

This is the part of billing that has to be right. The rules it enforces:

* **Only a verified webhook (or a direct reconciliation with the provider) can
  change a plan.** Nothing the browser sends is trusted; the fake gateway used in
  tests has to satisfy the same signature step as Stripe.
* **Every event is processed at most once.** Stripe delivers duplicates, and a
  duplicate that re-grants a plan is at best noise and at worst a free upgrade.
* **A transient payment problem must not revoke access.** `past_due` means Stripe
  is still retrying; only terminal statuses downgrade.
* **Entitlement and billing state are stored separately.** Losing track of the
  price id must not silently downgrade someone who is paying.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.features.billing.gateway import BillingGateway, SubscriptionSnapshot
from app.features.billing.models import PLAN_FREE, BillingEvent
from app.features.billing.settings import BillingSettings, get_billing_settings

log = logging.getLogger("quicklaunch.billing")

# Events that can change a plan. Anything else is recorded and ignored, so a new
# Stripe event type cannot accidentally alter entitlements.
HANDLED_EVENTS = frozenset(
    {
        "checkout.session.completed",
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "invoice.paid",
    }
)

# Subscription statuses that mean "no longer entitled". Everything else
# (active, trialing, past_due, incomplete) keeps the plan.
TERMINAL_STATUSES = frozenset({"canceled", "unpaid", "incomplete_expired"})

# Plans we are willing to downgrade to. Anything unrecognised keeps the current
# plan, which is the safe direction.
_KNOWN_PLANS = ("free", "plus", "pro")


@dataclass
class WebhookOutcome:
    event_id: str
    event_type: str
    status: str  # "applied" | "duplicate" | "ignored" | "unmatched"
    detail: str = ""


def _now() -> datetime:
    return datetime.now(UTC)


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    try:
        return dict(value)
    except (TypeError, ValueError):
        return {}


def _metadata(obj: dict[str, Any]) -> dict[str, Any]:
    meta = obj.get("metadata")
    return meta if isinstance(meta, dict) else _as_dict(meta)


async def _user_by_customer(session: AsyncSession, customer_id: str) -> User | None:
    if not customer_id:
        return None
    return await session.scalar(select(User).where(User.stripe_customer_id == customer_id))


async def _user_by_email(session: AsyncSession, email: str) -> User | None:
    if not email:
        return None
    return await session.scalar(select(User).where(User.email == email.strip().lower()))


def _plan_for_snapshot(snapshot: SubscriptionSnapshot, settings: BillingSettings, current_plan: str) -> str:
    """Which plan this subscription grants.

    Prefers the price id. If the price is not one of ours - a price rotated in
    the Stripe dashboard, say - the existing paid plan is kept rather than
    dropped, so configuration drift cannot silently take away something the user
    is paying for. Only a user who was already on free stays on free.
    """
    by_price = settings.plan_for_price(snapshot.price_id)
    if by_price:
        return by_price
    if current_plan in ("plus", "pro"):
        return current_plan
    return PLAN_FREE


def apply_snapshot(user: User, snapshot: SubscriptionSnapshot, settings: BillingSettings) -> None:
    """Write a provider snapshot onto the user. Idempotent by construction."""
    user.stripe_subscription_id = snapshot.subscription_id or user.stripe_subscription_id
    user.stripe_customer_id = snapshot.customer_id or user.stripe_customer_id
    user.subscription_status = snapshot.status or user.subscription_status
    user.subscription_price_id = snapshot.price_id or user.subscription_price_id
    user.current_period_end = snapshot.current_period_end or user.current_period_end
    user.cancel_at_period_end = snapshot.cancel_at_period_end

    if snapshot.status in TERMINAL_STATUSES:
        user.plan = PLAN_FREE
        return
    if snapshot.is_active:
        user.plan = _plan_for_snapshot(snapshot, settings, user.plan)


async def _claim_event(
    session: AsyncSession, event_id: str, event_type: str, summary: str
) -> BillingEvent | None:
    """Insert the event id; None means another delivery already claimed it.

    A savepoint is used so the failed insert does not poison the surrounding
    transaction - without it, catching the IntegrityError would leave the session
    unusable.
    """
    event = BillingEvent(event_id=event_id, event_type=event_type, summary=summary)
    try:
        async with session.begin_nested():
            session.add(event)
            await session.flush()
    except IntegrityError:
        return None
    return event


async def process_webhook(
    session: AsyncSession,
    gateway: BillingGateway,
    event: dict[str, Any],
    settings: BillingSettings | None = None,
) -> WebhookOutcome:
    """Handle one already-verified event. Never raises for a business reason."""
    settings = settings or get_billing_settings()
    event_id = str(event.get("id") or "")
    event_type = str(event.get("type") or "")
    obj = _as_dict(event.get("object"))

    if not event_id:
        return WebhookOutcome("", event_type, "ignored", "event had no id")

    summary = f"{event_type} object={_as_dict(obj).get('id', '')}"
    claimed = await _claim_event(session, event_id, event_type, summary)
    if claimed is None:
        return WebhookOutcome(event_id, event_type, "duplicate", "already processed")

    if event_type not in HANDLED_EVENTS:
        await session.commit()
        return WebhookOutcome(event_id, event_type, "ignored", "not an entitlement event")

    user, snapshot, detail = await _resolve(session, gateway, event_type, obj)
    if user is None:
        # Kept deliberately: "unmatched" hides behind a 200, and knowing which
        # field was missing is the difference between a five-minute and an
        # hour-long diagnosis. It is also what finally located the mismatch
        # between the fake provider's event shape and Stripe's.
        log.warning(
            "billing event %s (%s) matched no user: %s | object keys=%s | client_reference_id=%r metadata=%r",
            event_id,
            event_type,
            detail,
            sorted(obj.keys()),
            obj.get("client_reference_id"),
            obj.get("metadata"),
        )
        await session.commit()
        return WebhookOutcome(event_id, event_type, "unmatched", detail)

    claimed.user_id = user.id
    apply_snapshot(user, snapshot, settings)
    await session.commit()
    return WebhookOutcome(
        event_id,
        event_type,
        "applied",
        f"{detail} plan={user.plan} status={snapshot.status}",
    )


async def _resolve(
    session: AsyncSession,
    gateway: BillingGateway,
    event_type: str,
    obj: dict[str, Any],
) -> tuple[User | None, SubscriptionSnapshot, str]:
    """Find whose entitlement this event describes, and what it should be."""
    if event_type == "checkout.session.completed":
        # The session points at a customer; the subscription itself is fetched so
        # the plan comes from the subscription, not from what was merely intended.
        customer_id = str(obj.get("customer") or "")
        subscription_id = str(obj.get("subscription") or "")
        metadata = _metadata(obj)
        user_id = str(obj.get("client_reference_id") or metadata.get("user_id") or "")

        user = await session.get(User, user_id) if user_id else None
        if user is None:
            user = await _user_by_customer(session, customer_id)
        if user is None:
            user = await _user_by_email(session, str(obj.get("customer_email") or ""))
        if user is None:
            return None, _empty_snapshot(customer_id), "no user matched the checkout session"

        if customer_id:
            user.stripe_customer_id = customer_id
        snapshot = gateway.get_subscription(subscription_id) if subscription_id else None
        if snapshot is None:
            return None, _empty_snapshot(customer_id), "subscription not retrievable"
        return user, snapshot, "checkout.sessions.completed"

    if event_type.startswith("customer.subscription."):
        subscription_id = str(obj.get("id") or "")
        from_snapshot = snapshot_from_event_object(obj)
        customer_id = from_snapshot.customer_id
        # The subscription carries our user id in its metadata (set at checkout),
        # which is the most direct way to find the user before any customer id
        # has been stored.
        metadata_user_id = from_snapshot.user_id

        user = await session.get(User, metadata_user_id) if metadata_user_id else None
        if user is None:
            user = await _user_by_customer(session, customer_id)
        if user is None and subscription_id:
            user = await session.scalar(select(User).where(User.stripe_subscription_id == subscription_id))
        if user is None:
            return None, from_snapshot, "no user matched the subscription"
        return user, from_snapshot, event_type

    if event_type == "invoice.paid":
        # A renewal: the period end moved, so refresh it from the provider rather
        # than trusting the invoice's own copy.
        customer_id = str(obj.get("customer") or "")
        subscription_id = str(obj.get("subscription") or "")
        user = await _user_by_customer(session, customer_id)
        if user is None:
            return None, _empty_snapshot(customer_id), "no user matched the invoice"
        snapshot = gateway.get_subscription(subscription_id) if subscription_id else None
        if snapshot is None:
            return None, _empty_snapshot(customer_id), "subscription not retrievable"
        return user, snapshot, event_type

    return None, _empty_snapshot(""), f"unhandled event {event_type}"


def _empty_snapshot(customer_id: str) -> SubscriptionSnapshot:
    return SubscriptionSnapshot(
        subscription_id="",
        customer_id=customer_id,
        status="",
        price_id="",
        current_period_end=None,
        cancel_at_period_end=False,
    )


def snapshot_from_event_object(obj: dict[str, Any]) -> SubscriptionSnapshot:
    """Read a subscription-shaped object out of an event.

    Deliberately mirrors `gateway.snapshot_from_stripe`, including the
    period-end fallback: recent Stripe API versions moved that field onto the
    subscription items, and taking whichever reports a value avoids a silent
    `None`.
    """
    from app.features.billing.gateway import _as_datetime  # local import keeps helpers private

    items = _as_dict(obj.get("items"))
    data = items.get("data") or []
    first = _as_dict(data[0]) if data else {}

    price_id = ""
    price = _as_dict(first.get("price"))
    if price.get("id"):
        price_id = str(price["id"])
    else:
        plan = _as_dict(first.get("plan"))
        price_id = str(plan.get("id") or "")

    period_end = _as_datetime(obj.get("current_period_end")) or _as_datetime(first.get("current_period_end"))
    if period_end is None:
        period_end = _as_datetime(_as_dict(first.get("plan")).get("current_period_end"))

    return SubscriptionSnapshot(
        subscription_id=str(obj.get("id") or ""),
        customer_id=str(obj.get("customer") or ""),
        status=str(obj.get("status") or ""),
        price_id=price_id,
        current_period_end=period_end,
        cancel_at_period_end=bool(obj.get("cancel_at_period_end")),
        user_id=str(_as_dict(obj.get("metadata")).get("user_id") or ""),
    )


async def reconcile_user(
    session: AsyncSession, user: User, gateway: BillingGateway, settings: BillingSettings | None = None
) -> SubscriptionSnapshot | None:
    """Ask the provider what this user's subscription really is, and apply it.

    This is the safety net for a webhook that never arrived: the user clicks
    "I have paid" in the UI and the server checks Stripe directly. It is not a
    substitute for the webhook - it only runs when the user asks.
    """
    settings = settings or get_billing_settings()
    if not user.stripe_subscription_id:
        return None
    snapshot = gateway.get_subscription(user.stripe_subscription_id)
    if snapshot is None:
        return None
    apply_snapshot(user, snapshot, settings)
    await session.commit()
    return snapshot
