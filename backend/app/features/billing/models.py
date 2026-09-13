"""Billing 的表。

`BillingEvent` 从骨架的 `app/db/models.py` 搬过来：它是"支付方发过什么"的记账表，
只对 billing 有意义。迁移时删掉这个功能包，它也就不存在了。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utcnow

# Plans, in ascending order of what they allow. Stored as text so a new plan is a
# data change rather than a migration. 这些常量属于 billing：档位是"付了多少钱"的概念，
# 骨架只需要 `users.plan` 这一列的默认值，用字面量即可（不反向依赖功能包）。
PLAN_FREE = "free"
PLAN_PLUS = "plus"
PLAN_PRO = "pro"


class BillingEvent(Base):
    """Every Stripe webhook we accepted, keyed by Stripe's own event id.

    Stripe retries deliveries, and it can deliver the same event more than once
    even without retrying. Without this table a repeated
    `customer.subscription.created` would be processed twice - harmless for a
    plan flag, but not for anything that grants credit or sends mail. The primary
    key makes "have I seen this?" a single insert that either succeeds or
    violates the key, with no read-then-write race.
    """

    __tablename__ = "billing_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Nullable: an event for a customer we cannot map to a user is still worth
    # recording, with the reason, rather than silently dropped.
    user_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    # A short summary rather than the whole payload: enough to debug without
    # storing customer data we do not need.
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
