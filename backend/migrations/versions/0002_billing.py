"""billing: plans, subscription state and webhook idempotency

Revision ID: 0002_billing
Revises: 0001_init
Create Date: 2026-09-12 00:00:00

Additive only: every new column is nullable or has a server-side default, so an
existing deployment can be migrated forward and - because the columns carry no
data that 0001 needs - back again. `alembic downgrade` is exercised in CI.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_billing"
down_revision: str | None = "0001_init"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SUBSCRIPTION_COLUMNS = (
    sa.Column("stripe_customer_id", sa.String(length=64), nullable=True),
    sa.Column("stripe_subscription_id", sa.String(length=64), nullable=True),
    sa.Column("subscription_status", sa.String(length=32), nullable=True),
    sa.Column("subscription_price_id", sa.String(length=64), nullable=True),
    sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
)


def upgrade() -> None:
    # `plan` defaults to the free tier, so existing rows land on it without a
    # data migration step.
    op.add_column(
        "users",
        sa.Column("plan", sa.String(length=16), nullable=False, server_default="free"),
    )
    op.add_column(
        "users", sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    for column in SUBSCRIPTION_COLUMNS:
        op.add_column("users", column)

    # One customer maps to one user; the unique index is what makes a webhook
    # lookup unambiguous.
    op.create_index("ix_users_stripe_customer_id", "users", ["stripe_customer_id"], unique=True)
    op.create_index("ix_users_stripe_subscription_id", "users", ["stripe_subscription_id"])

    op.create_table(
        "billing_events",
        sa.Column("event_id", sa.String(length=64), primary_key=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_billing_events_event_type", "billing_events", ["event_type"])
    op.create_index("ix_billing_events_user_id", "billing_events", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_billing_events_user_id", table_name="billing_events")
    op.drop_index("ix_billing_events_event_type", table_name="billing_events")
    op.drop_table("billing_events")

    op.drop_index("ix_users_stripe_subscription_id", table_name="users")
    op.drop_index("ix_users_stripe_customer_id", table_name="users")
    for column in reversed(SUBSCRIPTION_COLUMNS):
        op.drop_column("users", column.name)
    op.drop_column("users", "cancel_at_period_end")
    op.drop_column("users", "plan")
