"""Payment-provider parsing, on the shapes Stripe actually sends.

The integration tests drive the fake gateway, which is the right tool for the
application logic - but it means the *parsing* of real Stripe payloads would never
be exercised. A bug there (a field that moved between API versions, a nested
price lookup) would only surface in production.

So these tests build subscription objects by hand, in both the old and new
layouts, and check that they normalise to the same snapshot. No network, no SDK
objects required - which is also why they keep running if the SDK changes under
us.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.features.billing.gateway import snapshot_from_stripe
from app.features.billing.service import snapshot_from_event_object
from app.features.billing.settings import get_billing_settings
from app.features.todos.settings import get_todo_settings

pytestmark = pytest.mark.unit

PERIOD_END = 1_800_000_000  # arbitrary, but far enough out to be a real timestamp


def _expected_end() -> datetime:
    return datetime.fromtimestamp(PERIOD_END, tz=UTC)


class TestSubscriptionShapes:
    """`current_period_end` has moved between the subscription and its items."""

    def test_period_end_on_the_subscription_itself(self) -> None:
        """The older layout."""
        snapshot = snapshot_from_stripe(
            {
                "id": "sub_1",
                "customer": "cus_1",
                "status": "active",
                "current_period_end": PERIOD_END,
                "cancel_at_period_end": False,
                "metadata": {"user_id": "u_1"},
                "items": {"data": [{"price": {"id": "price_plus"}}]},
            }
        )
        assert snapshot.current_period_end == _expected_end()
        assert snapshot.price_id == "price_plus"
        assert snapshot.user_id == "u_1"

    def test_period_end_on_the_first_item(self) -> None:
        """The newer layout, where the period belongs to the item."""
        snapshot = snapshot_from_stripe(
            {
                "id": "sub_2",
                "customer": "cus_2",
                "status": "active",
                "cancel_at_period_end": True,
                "metadata": {"user_id": "u_2"},
                "items": {"data": [{"price": {"id": "price_pro"}, "current_period_end": PERIOD_END}]},
            }
        )
        assert snapshot.current_period_end == _expected_end(), (
            "a period end on the item must not be missed - that silently drops the renewal date the UI shows"
        )
        assert snapshot.price_id == "price_pro"
        assert snapshot.cancel_at_period_end is True

    def test_both_layouts_agree(self) -> None:
        old = snapshot_from_stripe(
            {
                "id": "sub",
                "customer": "cus",
                "status": "active",
                "current_period_end": PERIOD_END,
                "items": {"data": [{"price": {"id": "p"}}]},
            }
        )
        new = snapshot_from_stripe(
            {
                "id": "sub",
                "customer": "cus",
                "status": "active",
                "items": {"data": [{"price": {"id": "p"}, "current_period_end": PERIOD_END}]},
            }
        )
        assert old.current_period_end == new.current_period_end
        assert old.price_id == new.price_id

    def test_the_subscription_wins_when_both_are_present(self) -> None:
        snapshot = snapshot_from_stripe(
            {
                "id": "sub",
                "customer": "cus",
                "status": "active",
                "current_period_end": PERIOD_END,
                "items": {"data": [{"current_period_end": PERIOD_END + 99999}]},
            }
        )
        assert snapshot.current_period_end == _expected_end()

    def test_a_missing_period_end_is_none_not_an_error(self) -> None:
        snapshot = snapshot_from_stripe({"id": "sub", "customer": "cus", "status": "active"})
        assert snapshot.current_period_end is None
        assert snapshot.price_id == ""

    def test_an_iso_string_period_end_is_parsed(self) -> None:
        """Tolerated even though Stripe sends unix seconds."""
        snapshot = snapshot_from_stripe(
            {"id": "s", "customer": "c", "status": "active", "current_period_end": "2027-01-01T00:00:00Z"}
        )
        assert snapshot.current_period_end is not None
        assert snapshot.current_period_end.year == 2027

    def test_a_legacy_plan_nested_price_is_read(self) -> None:
        """Old subscriptions carry `plan.id` where new ones carry `price.id`."""
        snapshot = snapshot_from_stripe(
            {
                "id": "s",
                "customer": "c",
                "status": "active",
                "items": {"data": [{"plan": {"id": "price_legacy", "current_period_end": PERIOD_END}}]},
            }
        )
        assert snapshot.price_id == "price_legacy"
        assert snapshot.current_period_end == _expected_end()


class TestEntitlementStatuses:
    def test_active_statuses_entitle(self) -> None:
        for status in ("active", "trialing", "past_due"):
            snapshot = snapshot_from_stripe({"id": "s", "customer": "c", "status": status})
            assert snapshot.is_active, f"{status} should keep access"

    def test_terminal_statuses_do_not(self) -> None:
        for status in ("canceled", "unpaid", "incomplete_expired"):
            snapshot = snapshot_from_stripe({"id": "s", "customer": "c", "status": status})
            assert not snapshot.is_active, f"{status} should not keep access"


class TestEventObjectParsing:
    """The same normalisation, applied to a subscription inside a webhook."""

    def test_item_period_end_and_price(self) -> None:
        snapshot = snapshot_from_event_object(
            {
                "id": "sub_evt",
                "customer": "cus_evt",
                "status": "active",
                "cancel_at_period_end": False,
                "metadata": {"user_id": "u_evt"},
                "items": {"data": [{"price": {"id": "price_plus"}, "current_period_end": PERIOD_END}]},
            }
        )
        assert snapshot.subscription_id == "sub_evt"
        assert snapshot.customer_id == "cus_evt"
        assert snapshot.price_id == "price_plus"
        assert snapshot.current_period_end == _expected_end()
        assert snapshot.user_id == "u_evt"

    def test_missing_metadata_yields_no_user(self) -> None:
        snapshot = snapshot_from_event_object({"id": "s", "customer": "c", "status": "active"})
        assert snapshot.user_id == ""

    def test_an_empty_object_does_not_raise(self) -> None:
        snapshot = snapshot_from_event_object({})
        assert snapshot.subscription_id == ""
        assert snapshot.status == ""
        assert snapshot.current_period_end is None


class TestPriceMapping:
    def test_prices_map_to_plans_and_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = get_billing_settings()
        monkeypatch.setattr(settings, "stripe_price_plus", "price_plus_x", raising=False)
        monkeypatch.setattr(settings, "stripe_price_pro", "price_pro_x", raising=False)

        assert settings.price_id_for("plus") == "price_plus_x"
        assert settings.price_id_for("pro") == "price_pro_x"
        assert settings.price_id_for("free") == ""
        assert settings.plan_for_price("price_pro_x") == "pro"
        assert settings.plan_for_price("price_someone_elses") is None

    def test_quotas_match_the_agreed_tiers(self) -> None:
        assert get_todo_settings().todo_limit_for("free") == 10
        assert get_todo_settings().todo_limit_for("plus") == 200
        assert get_todo_settings().todo_limit_for("pro") is None, "pro means unlimited"

    def test_an_unknown_plan_falls_back_to_the_free_limit(self) -> None:
        """Safer than unlimited: a typo in the database must not remove the cap."""
        assert get_todo_settings().todo_limit_for("enterprise") == 10
