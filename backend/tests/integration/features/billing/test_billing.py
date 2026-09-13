"""Billing end to end through the API, with the in-memory provider.

These run in the same suite as everything else and never touch the network. What
they are really guarding:

* a plan only ever changes because of a **verified** webhook;
* a replayed webhook does not grant anything twice;
* hitting the free limit is a 402 the UI can act on, and a paid plan lifts it;
* a transient payment problem must not take access away, while a terminal one must.
"""

from __future__ import annotations

import json

import pytest
from app.features.billing.fake import FakeGateway
from fastapi.testclient import TestClient

from tests.conftest import register

pytestmark = pytest.mark.integration

FREE_LIMIT = 10


def _emit(client: TestClient, gateway: FakeGateway, event: dict) -> tuple[int, dict]:
    """Deliver one event the way Stripe would: signed, to /api/billing/webhook.

    The gateway's builders wrap the payload in `data.object` (Stripe's real
    shape) and the parser unwraps it, so this helper posts the dict unchanged.
    Posting an already-unwrapped payload would bypass the parse path - which is
    how a bug where every lookup returned an empty object stayed hidden.
    """
    payload = json.dumps(event).encode()
    signature = gateway.sign(payload)
    response = client.post(
        "/api/billing/webhook",
        content=payload,
        headers={"Stripe-Signature": signature, "Content-Type": "application/json"},
    )
    body = response.json() if response.content else {}
    return response.status_code, body


def _subscribe(client: TestClient, gateway: FakeGateway, plan: str = "plus") -> str:
    """Take a user through checkout and the webhooks Stripe would send."""
    assert client.post("/api/billing/checkout", json={"plan": plan}).status_code == 200
    session_id = gateway.checkout_calls[-1]["session_id"]
    subscription_id = gateway.complete_checkout(session_id)

    for event in (
        gateway.checkout_completed_event(session_id),
        gateway.subscription_event(subscription_id, "customer.subscription.created"),
    ):
        status, body = _emit(client, gateway, event)
        assert status == 200
        assert body.get("status") == "applied", f"{event['type']} was not applied: {body}"
    return subscription_id


class TestQuota:
    def test_plans_and_quota_are_reported(self, anon: TestClient) -> None:
        register(anon, "quota@example.com")
        body = anon.get("/api/billing/me").json()

        assert body["plan"] == "free"
        assert body["quota"]["limit"] == FREE_LIMIT
        assert body["quota"]["used"] == 0
        assert body["quota"]["can_create"] is True
        assert [p["id"] for p in body["plans"]] == ["plus", "pro"]

    def test_free_plan_stops_at_the_limit_with_402(self, anon: TestClient) -> None:
        register(anon, "limit@example.com")
        for index in range(FREE_LIMIT):
            response = anon.post("/api/todos", json={"title": f"todo {index}"})
            assert response.status_code == 201, f"todo {index} was rejected: {response.text}"

        blocked = anon.post("/api/todos", json={"title": "one too many"})
        assert blocked.status_code == 402
        assert blocked.json()["error"]["code"] == "quota_exceeded"

        # The message has to be actionable, not just a status code.
        assert str(FREE_LIMIT) in blocked.json()["error"]["message"]
        assert anon.get("/api/billing/me").json()["quota"]["can_create"] is False

    def test_reads_and_deletes_still_work_over_the_limit(self, anon: TestClient) -> None:
        """Being over the limit must not trap the data someone already has."""
        register(anon, "over@example.com")
        ids = [
            anon.post("/api/todos", json={"title": f"t{index}"}).json()["id"] for index in range(FREE_LIMIT)
        ]
        assert anon.post("/api/todos", json={"title": "blocked"}).status_code == 402

        assert len(anon.get("/api/todos").json()) == FREE_LIMIT
        assert anon.patch(f"/api/todos/{ids[0]}", json={"done": True}).status_code == 200
        assert anon.delete(f"/api/todos/{ids[0]}").status_code == 204
        # Room again, so creating works once more.
        assert anon.post("/api/todos", json={"title": "after delete"}).status_code == 201


class TestCheckout:
    def test_checkout_returns_a_provider_url_and_never_a_price_from_the_client(
        self, anon: TestClient, fake_gateway: FakeGateway
    ) -> None:
        register(anon, "checkout@example.com")
        response = anon.post("/api/billing/checkout", json={"plan": "plus"})

        assert response.status_code == 200
        assert response.json()["url"].startswith("https://checkout.example.test/")

        # The price the provider was asked for must be the server's, not the
        # caller's: the request only ever contained the word "plus".
        call = fake_gateway.checkout_calls[-1]
        assert call["price_id"] == "price_fake_plus"
        assert call["user_id"]

    def test_an_unknown_plan_is_rejected(self, anon: TestClient) -> None:
        register(anon, "unknown@example.com")
        assert anon.post("/api/billing/checkout", json={"plan": "enterprise"}).status_code == 422

    def test_checking_out_for_the_current_plan_is_a_conflict(
        self, anon: TestClient, fake_gateway: FakeGateway
    ) -> None:
        register(anon, "already@example.com")
        _subscribe(anon, fake_gateway, "plus")

        response = anon.post("/api/billing/checkout", json={"plan": "plus"})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "already_on_plan"

    def test_portal_needs_a_subscription_first(self, anon: TestClient) -> None:
        register(anon, "portal@example.com")
        response = anon.post("/api/billing/portal")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "no_subscription"

    def test_portal_opens_once_a_customer_exists(self, anon: TestClient, fake_gateway: FakeGateway) -> None:
        register(anon, "portal2@example.com")
        _subscribe(anon, fake_gateway, "plus")

        response = anon.post("/api/billing/portal")
        assert response.status_code == 200
        assert fake_gateway.portal_calls[-1]["customer_id"]


class TestWebhookSignature:
    def test_a_missing_signature_is_rejected(self, client: TestClient) -> None:
        response = client.post("/api/billing/webhook", content=b'{"id":"evt_1"}')
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "missing_signature"

    def test_a_tampered_payload_is_rejected(self, client: TestClient, fake_gateway: FakeGateway) -> None:
        payload = json.dumps({"id": "evt_1", "type": "invoice.paid", "object": {}}).encode()
        signature = fake_gateway.sign(payload)
        tampered = json.dumps({"id": "evt_1", "type": "invoice.paid", "object": {"x": 1}}).encode()

        response = client.post(
            "/api/billing/webhook",
            content=tampered,
            headers={"Stripe-Signature": signature},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_signature"

    def test_a_replayed_signature_outside_the_tolerance_is_rejected(
        self, client: TestClient, fake_gateway: FakeGateway
    ) -> None:
        payload = json.dumps({"id": "evt_old", "type": "invoice.paid", "object": {}}).encode()
        # Signed correctly, but an hour ago: the tolerance must still catch it,
        # otherwise a captured request stays valid forever.
        stale = fake_gateway.sign(payload, timestamp=int(fake_gateway.now()) - 3600)

        response = client.post("/api/billing/webhook", content=payload, headers={"Stripe-Signature": stale})
        assert response.status_code == 400
        assert "tolerance" in response.json()["error"]["message"]


class TestEntitlement:
    def test_a_paid_plan_lifts_the_limit(self, anon: TestClient, fake_gateway: FakeGateway) -> None:
        register(anon, "upgrade@example.com")
        for index in range(FREE_LIMIT):
            anon.post("/api/todos", json={"title": f"t{index}"})
        assert anon.post("/api/todos", json={"title": "blocked"}).status_code == 402

        _subscribe(anon, fake_gateway, "plus")

        body = anon.get("/api/billing/me").json()
        assert body["plan"] == "plus"
        assert body["quota"]["limit"] == 200
        assert anon.post("/api/todos", json={"title": "now allowed"}).status_code == 201

    def test_pro_is_unlimited(self, anon: TestClient, fake_gateway: FakeGateway) -> None:
        register(anon, "pro@example.com")
        _subscribe(anon, fake_gateway, "pro")

        body = anon.get("/api/billing/me").json()
        assert body["plan"] == "pro"
        assert body["quota"]["limit"] is None
        assert body["quota"]["remaining"] is None

    def test_a_duplicate_event_does_not_grant_anything_twice(
        self, anon: TestClient, fake_gateway: FakeGateway
    ) -> None:
        register(anon, "dupe@example.com")
        anon.post("/api/billing/checkout", json={"plan": "plus"})
        session_id = fake_gateway.checkout_calls[-1]["session_id"]
        subscription_id = fake_gateway.complete_checkout(session_id)

        event = fake_gateway.subscription_event(subscription_id, "customer.subscription.created")
        first_status, first = _emit(anon, fake_gateway, event)
        second_status, second = _emit(anon, fake_gateway, event)

        assert first_status == second_status == 200
        assert first["status"] == "applied"
        # Reported as a duplicate, and - the point - nothing changed the second time.
        assert second["status"] == "duplicate"

    def test_past_due_keeps_access_because_stripe_is_still_retrying(
        self, anon: TestClient, fake_gateway: FakeGateway
    ) -> None:
        register(anon, "pastdue@example.com")
        subscription_id = _subscribe(anon, fake_gateway, "plus")

        fake_gateway.set_subscription(subscription_id, status="past_due")
        _emit(
            anon,
            fake_gateway,
            fake_gateway.subscription_event(subscription_id, "customer.subscription.updated"),
        )

        body = anon.get("/api/billing/me").json()
        assert body["plan"] == "plus", "a failed renewal attempt must not revoke access"
        assert body["status"] == "past_due"

    def test_cancelling_downgrades_to_free_without_deleting_todos(
        self, anon: TestClient, fake_gateway: FakeGateway
    ) -> None:
        register(anon, "cancel@example.com")
        subscription_id = _subscribe(anon, fake_gateway, "plus")
        for index in range(FREE_LIMIT + 2):
            assert anon.post("/api/todos", json={"title": f"t{index}"}).status_code == 201

        fake_gateway.set_subscription(subscription_id, status="canceled")
        _emit(
            anon,
            fake_gateway,
            fake_gateway.subscription_event(subscription_id, "customer.subscription.deleted"),
        )

        body = anon.get("/api/billing/me").json()
        assert body["plan"] == "free"
        assert body["quota"]["used"] == FREE_LIMIT + 2, "existing todos must survive a downgrade"
        assert body["quota"]["can_create"] is False
        assert len(anon.get("/api/todos").json()) == FREE_LIMIT + 2

    def test_the_webhook_is_the_only_way_to_get_a_plan(
        self, anon: TestClient, fake_gateway: FakeGateway
    ) -> None:
        """Starting a checkout must not be enough on its own."""
        register(anon, "wishful@example.com")
        anon.post("/api/billing/checkout", json={"plan": "plus"})
        session_id = fake_gateway.checkout_calls[-1]["session_id"]
        fake_gateway.complete_checkout(session_id)

        # Paid at the provider, but no webhook has arrived yet.
        assert anon.get("/api/billing/me").json()["plan"] == "free"

    def test_sync_reconciles_when_the_webhook_never_arrived(
        self, anon: TestClient, fake_gateway: FakeGateway
    ) -> None:
        """The safety net: ask the provider directly, on the user's request."""
        register(anon, "lost@example.com")
        anon.post("/api/billing/checkout", json={"plan": "plus"})
        session_id = fake_gateway.checkout_calls[-1]["session_id"]
        subscription_id = fake_gateway.complete_checkout(session_id)
        # Deliberately skip the webhook; only record the linkage a checkout
        # session would have created.
        _emit(anon, fake_gateway, fake_gateway.checkout_completed_event(session_id))
        assert anon.get("/api/billing/me").json()["plan"] in {"free", "plus"}

        fake_gateway.set_subscription(subscription_id, status="active", price_id="price_fake_pro")
        response = anon.post("/api/billing/sync")

        assert response.status_code == 200
        assert response.json()["plan"] == "pro"
        assert anon.get("/api/billing/me").json()["plan"] == "pro"
