"""Billing package: provider access, entitlement rules and the fake used in tests.

对外只通过 `FEATURE` 交代一句"我叫 billing，路由是这个"——骨架不认识这里的任何东西。
"""

from app.features import Feature
from app.features.billing.gateway import (
    BillingGateway,
    BillingUnavailable,
    CheckoutSession,
    StripeGateway,
    SubscriptionSnapshot,
    normalise_event,
    snapshot_from_stripe,
)
from app.features.billing.router import router
from app.features.billing.service import (
    HANDLED_EVENTS,
    WebhookOutcome,
    apply_snapshot,
    process_webhook,
    reconcile_user,
)

FEATURE = Feature(name="billing", router=router)

__all__ = [
    "FEATURE",
    "HANDLED_EVENTS",
    "BillingGateway",
    "BillingUnavailable",
    "CheckoutSession",
    "StripeGateway",
    "SubscriptionSnapshot",
    "WebhookOutcome",
    "apply_snapshot",
    "normalise_event",
    "process_webhook",
    "reconcile_user",
    "snapshot_from_stripe",
]
