"""Billing request and response contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# 配额契约属于 todos（"还能建几条待办"），billing 只是展示它。
# 这里是**再导出**，不是重新定义——两份定义迟早会分叉。
from app.features.todos.schemas import QuotaOut

__all__ = [
    "BillingMeOut",
    "CheckoutOut",
    "CheckoutRequest",
    "PlanOut",
    "PortalOut",
    "QuotaOut",
    "SyncOut",
]


class PlanOut(BaseModel):
    """A purchasable plan, so the UI hardcodes neither names nor prices."""

    id: str
    name: str
    limit: int | None
    price_id: str
    available: bool


class BillingMeOut(BaseModel):
    plan: str
    quota: QuotaOut
    # Mirrored verbatim from the provider; shown as "renews on ...".
    status: str | None = None
    current_period_end: datetime | None = None
    cancel_at_period_end: bool = False
    has_customer: bool = False
    plans: list[PlanOut]
    billing_enabled: bool


class CheckoutRequest(BaseModel):
    # A plan name, never a price id: the mapping to a price happens server-side.
    plan: str = Field(pattern="^(plus|pro)$")


class CheckoutOut(BaseModel):
    url: str


class PortalOut(BaseModel):
    url: str


class SyncOut(BaseModel):
    plan: str
    status: str | None = None
    current_period_end: datetime | None = None
    changed: bool
