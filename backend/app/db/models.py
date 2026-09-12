"""ORM models: a user, that user's todos, and the billing state behind a plan.

Kept deliberately small. The subscription columns live on `users` rather than in
a subscriptions table because a user has at most one subscription here; a second
table would only add a join.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, new_id, utcnow

# Plans, in ascending order of what they allow. Stored as text so a new plan is a
# data change rather than a migration.
PLAN_FREE = "free"
PLAN_PLUS = "plus"
PLAN_PRO = "pro"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    # Stored lower-cased; uniqueness is what makes login unambiguous.
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    # --- entitlement ------------------------------------------------------
    # What the user is allowed to do. Only ever changed by a verified webhook
    # (or by the reconcile endpoint asking Stripe directly), never by the client.
    plan: Mapped[str] = mapped_column(String(16), default=PLAN_FREE, nullable=False)

    # --- billing state, mirrored from Stripe ------------------------------
    # Ids are stored so a webhook can find the user, and so /billing/sync can ask
    # Stripe about this customer without trusting anything the browser sent.
    stripe_customer_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    # Stripe's own vocabulary (active, trialing, past_due, canceled, ...). Stored
    # verbatim so no mapping layer can lose information.
    subscription_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    subscription_price_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    todos: Mapped[list[Todo]] = relationship(back_populates="user", cascade="all, delete-orphan")
    billing_events: Mapped[list[BillingEvent]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def is_paid(self) -> bool:
        return self.plan in (PLAN_PLUS, PLAN_PRO)


class Todo(Base):
    __tablename__ = "todos"
    # The single hot query is "my todos, newest first" -> one composite index.
    __table_args__ = (Index("ix_todos_user_created", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    done: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Timestamps are generated in Python rather than by the database: sqlite's
    # CURRENT_TIMESTAMP only has second resolution, which would make "newest
    # first" ambiguous for rows created in the same second.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="todos")


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

    user: Mapped[User | None] = relationship(back_populates="billing_events")
