"""The real SDK's signature verification, exercised without a network.

The fake gateway has its own HMAC implementation, which is fine for driving the
application - but it means the *real* signature path would only ever run in
production. These tests call `stripe.Webhook.construct_event`, the same function
the StripeGateway uses, so the verification the deployment relies on is the
verification under test.

No network is involved: signing and verifying are both local operations.
"""

from __future__ import annotations

import json
import time

import pytest
import stripe

pytestmark = pytest.mark.unit

SECRET = "whsec_unit_test_secret"
TOLERANCE = 300


def build(payload: bytes, secret: str = SECRET, timestamp: int | None = None) -> str:
    """Build the `Stripe-Signature` header exactly as Stripe does.

    Uses the SDK's own signing helper, so this is the scheme `stripe listen`
    produces rather than a re-implementation that could agree with itself while
    disagreeing with Stripe.
    """
    stamp = int(timestamp if timestamp is not None else time.time())
    signature = stripe.WebhookSignature._compute_signature(  # noqa: SLF001 - the documented scheme
        f"{stamp}.{payload.decode()}", secret
    )
    return f"t={stamp},v1={signature}"


EVENT = {
    "id": "evt_unit_1",
    "type": "customer.subscription.created",
    "data": {"object": {"id": "sub_1", "customer": "cus_1", "status": "active"}},
}
PAYLOAD = json.dumps(EVENT).encode()


class TestRealSignatureVerification:
    def test_a_correctly_signed_event_verifies(self) -> None:
        event = stripe.Webhook.construct_event(PAYLOAD, build(PAYLOAD), SECRET, tolerance=TOLERANCE)
        assert event["id"] == "evt_unit_1"
        assert event["type"] == "customer.subscription.created"

    def test_a_signature_from_another_secret_is_rejected(self) -> None:
        wrong = build(PAYLOAD, secret="whsec_someone_else")
        with pytest.raises(stripe.SignatureVerificationError):
            stripe.Webhook.construct_event(PAYLOAD, wrong, SECRET, tolerance=TOLERANCE)

    def test_a_tampered_payload_is_rejected(self) -> None:
        signature = build(PAYLOAD)
        tampered = json.dumps({**EVENT, "type": "invoice.paid"}).encode()
        with pytest.raises(stripe.SignatureVerificationError):
            stripe.Webhook.construct_event(tampered, signature, SECRET, tolerance=TOLERANCE)

    def test_a_stale_timestamp_is_rejected(self) -> None:
        """Otherwise a captured request stays valid forever."""
        old = build(PAYLOAD, timestamp=int(time.time()) - 3600)
        with pytest.raises(stripe.SignatureVerificationError):
            stripe.Webhook.construct_event(PAYLOAD, old, SECRET, tolerance=TOLERANCE)

    def test_the_sdk_by_itself_accepts_a_future_timestamp(self) -> None:
        """Documents why StripeGateway adds its own check.

        Measured against stripe-python 15.6.1: `construct_event` only rejects
        timestamps that are too OLD. A header dated an hour ahead verifies fine,
        so a captured request could be replayed indefinitely by editing `t`
        upward - the timestamp is part of what is signed, so the signature stays
        valid. If a future SDK tightens this, this test fails and the extra guard
        becomes redundant rather than silently wrong.
        """
        ahead = build(PAYLOAD, timestamp=int(time.time()) + 3600)
        event = stripe.Webhook.construct_event(PAYLOAD, ahead, SECRET, tolerance=TOLERANCE)
        assert event["id"] == "evt_unit_1"

    def test_the_gateway_rejects_a_future_timestamp_anyway(self) -> None:
        """The guard the deployment relies on, since the SDK does not provide it."""
        from app.billing.gateway import _reject_future_timestamp

        ahead = build(PAYLOAD, timestamp=int(time.time()) + 3600)
        with pytest.raises(ValueError, match="future"):
            _reject_future_timestamp(ahead, TOLERANCE)

        # ...and a header inside the tolerance is left alone.
        _reject_future_timestamp(build(PAYLOAD, timestamp=int(time.time()) + 10), TOLERANCE)

    def test_a_header_without_a_usable_timestamp_falls_through_to_the_sdk(self) -> None:
        from app.billing.gateway import _reject_future_timestamp

        # No `t=` at all: the guard stays out of the way so the SDK's error is the
        # one reported, rather than a guess of ours.
        _reject_future_timestamp("v1=deadbeef", TOLERANCE)
        _reject_future_timestamp("", TOLERANCE)

    def test_both_gateways_share_one_verification_implementation(self) -> None:
        """A test must not pass against a weaker check than production runs."""
        import inspect

        from app.billing.gateway import StripeGateway, verify_signature
        from app.billing.http_provider import HttpStripeGateway

        for gateway_class in (StripeGateway, HttpStripeGateway):
            source = inspect.getsource(gateway_class.parse_webhook)
            assert "verify_signature" in source, (
                f"{gateway_class.__name__} must delegate to the shared verifier"
            )
        assert "stripe" in inspect.getsource(verify_signature)

    def test_a_malformed_header_is_rejected(self) -> None:
        for bad in ("", "garbage", "t=123", "v1=abc"):
            with pytest.raises((stripe.SignatureVerificationError, ValueError)):
                stripe.Webhook.construct_event(PAYLOAD, bad, SECRET, tolerance=TOLERANCE)


class TestEventNormalisation:
    """`normalise_event` is what lets the service ignore the SDK's object types."""

    def test_the_sdk_object_normalises_to_the_shape_the_service_reads(self) -> None:
        from app.billing.gateway import normalise_event

        event = stripe.Webhook.construct_event(PAYLOAD, build(PAYLOAD), SECRET, tolerance=TOLERANCE)
        normalised = normalise_event(event)

        assert normalised["id"] == "evt_unit_1"
        assert normalised["type"] == "customer.subscription.created"
        assert isinstance(normalised["object"], dict), "the service must get a dict, not a StripeObject"
        assert normalised["object"]["id"] == "sub_1"

    def test_a_plain_dict_passes_through(self) -> None:
        from app.billing.gateway import normalise_event

        normalised = normalise_event(
            {"id": "evt_2", "type": "invoice.paid", "data": {"object": {"id": "in_1"}}}
        )
        assert normalised == {"id": "evt_2", "type": "invoice.paid", "object": {"id": "in_1"}}

    def test_an_event_without_a_data_wrapper_does_not_raise(self) -> None:
        from app.billing.gateway import normalise_event

        normalised = normalise_event({"id": "evt_3", "type": "ping"})
        assert normalised["object"] == {}
