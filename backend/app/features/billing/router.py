"""Billing endpoints.

The shape of the trust here matters more than the code:

* `/checkout` and `/portal` are authenticated, and the **plan comes from the
  server's own price table** - the client sends the word "plus", never a price
  id, so it cannot choose what it pays.
* `/webhook` is deliberately not cookie-authenticated: Stripe cannot hold a
  session. It is authenticated by signature instead, and it is the only route
  that can grant a plan.
* `/sync` exists because webhooks can be lost. It asks the provider directly,
  using the subscription id already stored on the user, so nothing the browser
  says is trusted.

When billing is not configured these routes answer 503 rather than pretending: a
billing screen that silently does nothing is worse than one that says why.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response, status

from app.deps import CurrentUser, SessionDep, api_error
from app.features.billing.gateway import BillingGateway, BillingUnavailable, StripeGateway
from app.features.billing.http_provider import HttpStripeGateway
from app.features.billing.schemas import (
    BillingMeOut,
    CheckoutOut,
    CheckoutRequest,
    PlanOut,
    PortalOut,
    SyncOut,
)
from app.features.billing.service import process_webhook, reconcile_user
from app.features.billing.settings import get_billing_settings
from app.features.todos.quota import quota_for, used_todos
from app.features.todos.settings import get_todo_settings
from app.schemas import ErrorResponse

router = APIRouter(prefix="/billing", tags=["billing"])
settings = get_billing_settings()

PLAN_LABELS = {"plus": "Plus", "pro": "Pro"}


def get_gateway() -> BillingGateway:
    """The provider, or a 503 that says what is missing.

    Overridden in tests (with the in-memory fake) and selected by configuration
    when `STRIPE_API_BASE` points at `scripts/fake_stripe.py` - which is how CI
    exercises the real HTTP path without a Stripe account.
    """
    if not settings.stripe_enabled:
        raise api_error(
            "billing_unavailable",
            "Billing is not configured on this server.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    try:
        if not settings.stripe_uses_official_api:
            return HttpStripeGateway(settings)
        return StripeGateway(settings)
    except BillingUnavailable as exc:  # pragma: no cover - configuration error
        raise api_error("billing_unavailable", str(exc), status.HTTP_503_SERVICE_UNAVAILABLE) from exc


GatewayDep = Annotated[BillingGateway, Depends(get_gateway)]


# 配额（一个用户还能建几条待办）由 todos 功能包负责，这里只是展示：
# 迁移时若删掉 features/todos/，把 /billing/me 的 quota 字段一并删掉即可。


def available_plans() -> list[PlanOut]:
    return [
        PlanOut(
            id="plus",
            name=PLAN_LABELS["plus"],
            limit=get_todo_settings().plus_todo_limit,
            price_id=settings.stripe_price_plus,
            available=bool(settings.stripe_price_plus),
        ),
        PlanOut(
            id="pro",
            name=PLAN_LABELS["pro"],
            limit=None,
            price_id=settings.stripe_price_pro,
            available=bool(settings.stripe_price_pro),
        ),
    ]


@router.get("/me", response_model=BillingMeOut)
async def billing_me(user: CurrentUser, session: SessionDep) -> BillingMeOut:
    used = await used_todos(session, user.id)
    return BillingMeOut(
        plan=user.plan,
        quota=quota_for(user.plan, used),
        status=user.subscription_status,
        current_period_end=user.current_period_end,
        cancel_at_period_end=user.cancel_at_period_end,
        has_customer=bool(user.stripe_customer_id),
        plans=available_plans(),
        billing_enabled=settings.stripe_enabled,
    )


@router.post("/checkout", response_model=CheckoutOut, responses={503: {"model": ErrorResponse}})
async def create_checkout(
    payload: CheckoutRequest,
    user: CurrentUser,
    request: Request,
    gateway: GatewayDep,
) -> CheckoutOut:
    price_id = settings.price_id_for(payload.plan)
    if not price_id:
        raise api_error(
            "unknown_plan",
            f"No price is configured for plan '{payload.plan}'.",
            status.HTTP_400_BAD_REQUEST,
        )
    if user.plan == payload.plan:
        label = PLAN_LABELS.get(payload.plan, payload.plan)
        raise api_error("already_on_plan", f"You are already on {label}.", status.HTTP_409_CONFLICT)

    # Relative success/cancel paths are resolved against the request that made
    # them, so a deployment does not need to know its own public URL.
    origin = str(request.base_url).rstrip("/")
    try:
        checkout = gateway.create_checkout_session(
            user_id=user.id,
            email=user.email,
            price_id=price_id,
            customer_id=user.stripe_customer_id,
            success_url=f"{origin}{settings.stripe_success_path}",
            cancel_url=f"{origin}{settings.stripe_cancel_path}",
        )
    except BillingUnavailable as exc:
        raise api_error("billing_unavailable", str(exc), status.HTTP_503_SERVICE_UNAVAILABLE) from exc
    return CheckoutOut(url=checkout.url)


@router.post("/portal", response_model=PortalOut, responses={503: {"model": ErrorResponse}})
async def create_portal(user: CurrentUser, request: Request, gateway: GatewayDep) -> PortalOut:
    if not user.stripe_customer_id:
        raise api_error(
            "no_subscription", "There is no subscription to manage yet.", status.HTTP_409_CONFLICT
        )
    origin = str(request.base_url).rstrip("/")
    try:
        url = gateway.create_portal_session(customer_id=user.stripe_customer_id, return_url=f"{origin}/")
    except BillingUnavailable as exc:
        raise api_error("billing_unavailable", str(exc), status.HTTP_503_SERVICE_UNAVAILABLE) from exc
    return PortalOut(url=url)


@router.post("/sync", response_model=SyncOut)
async def sync_subscription(user: CurrentUser, session: SessionDep, gateway: GatewayDep) -> SyncOut:
    """Reconcile with the provider, for when a webhook never arrived."""
    before = user.plan
    snapshot = await reconcile_user(session, user, gateway)
    if snapshot is None:
        raise api_error(
            "no_subscription",
            "No subscription is associated with this account yet.",
            status.HTTP_409_CONFLICT,
        )
    return SyncOut(
        plan=user.plan,
        status=user.subscription_status,
        current_period_end=user.current_period_end,
        changed=user.plan != before,
    )


@router.post("/webhook", include_in_schema=False)
async def stripe_webhook(
    request: Request,
    session: SessionDep,
    gateway: GatewayDep,
    stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
) -> Response:
    """The only thing that grants a plan: signature-verified, then idempotent."""
    if not stripe_signature:
        raise api_error("missing_signature", "Missing Stripe-Signature header.", status.HTTP_400_BAD_REQUEST)

    payload = await request.body()
    try:
        event = gateway.parse_webhook(payload, stripe_signature)
    except ValueError as exc:
        # Covers a wrong signature and a timestamp outside the tolerance alike.
        raise api_error(
            "invalid_signature",
            f"Signature verification failed: {exc}",
            status.HTTP_400_BAD_REQUEST,
        ) from exc

    outcome = await process_webhook(session, gateway, event)
    # 200 for duplicates and for events ignored on purpose: Stripe retries
    # anything else, and retrying a duplicate forever helps nobody.
    return Response(
        status_code=status.HTTP_200_OK,
        content=json.dumps({"status": outcome.status, "event": outcome.event_type, "detail": outcome.detail}),
        media_type="application/json",
    )
