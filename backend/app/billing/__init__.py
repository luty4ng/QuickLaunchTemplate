"""Billing package: provider access, entitlement rules and the fake used in tests."""

from app.billing.gateway import (
    BillingGateway,
    BillingUnavailable,
    CheckoutSession,
    StripeGateway,
    SubscriptionSnapshot,
    normalise_event,
    snapshot_from_stripe,
)
from app.billing.service import (
    HANDLED_EVENTS,
    WebhookOutcome,
    apply_snapshot,
    process_webhook,
    reconcile_user,
)

__all__ = [
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
