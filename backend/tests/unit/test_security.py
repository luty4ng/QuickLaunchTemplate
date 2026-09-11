"""Pure unit tests: hashing, tokens, validation. No database, no HTTP."""

from __future__ import annotations

import time

import jwt
import pytest
from app.config import get_settings
from app.schemas import Credentials, TodoCreate, TodoUpdate
from app.security import (
    create_session_token,
    decode_session_token,
    hash_password,
    verify_password,
)
from pydantic import ValidationError


class TestPasswordHashing:
    def test_hash_is_salted_and_verifies(self) -> None:
        first = hash_password("correct horse battery")
        second = hash_password("correct horse battery")
        assert first != second, "two hashes of the same password must differ (salt)"
        assert verify_password("correct horse battery", first)
        assert verify_password("correct horse battery", second)

    def test_wrong_password_is_rejected(self) -> None:
        assert not verify_password("nope", hash_password("correct horse battery"))

    def test_malformed_hash_does_not_raise(self) -> None:
        assert not verify_password("anything", "not-a-bcrypt-hash")
        assert not verify_password("anything", "")

    def test_long_password_is_truncated_not_fatal(self) -> None:
        # bcrypt caps at 72 bytes; the API must not 500 on a long password.
        long_password = "a" * 300
        assert verify_password(long_password, hash_password(long_password))
        assert verify_password("a" * 72, hash_password(long_password))


class TestSessionTokens:
    def test_round_trip(self) -> None:
        token = create_session_token("user-123")
        assert decode_session_token(token) == "user-123"

    def test_expired_token_is_rejected(self) -> None:
        assert decode_session_token(create_session_token("user-123", ttl_seconds=-1)) is None

    def test_tampered_signature_is_rejected(self) -> None:
        token = create_session_token("user-123")
        header, payload, signature = token.split(".")
        flipped = "A" if signature[0] != "A" else "B"
        assert decode_session_token(f"{header}.{payload}.{flipped}{signature[1:]}") is None

    def test_token_signed_with_another_secret_is_rejected(self) -> None:
        settings = get_settings()
        forged = jwt.encode(
            {"sub": "user-123", "exp": int(time.time()) + 600},
            "some-other-secret",
            algorithm=settings.jwt_alg,
        )
        assert decode_session_token(forged) is None

    def test_garbage_is_rejected(self) -> None:
        assert decode_session_token("") is None
        assert decode_session_token("not.a.jwt") is None


class TestSchemas:
    @pytest.mark.parametrize("email", ["", "not-an-email", "a@", "@b.com", "a b@c.com"])
    def test_invalid_emails_are_rejected(self, email: str) -> None:
        with pytest.raises(ValidationError):
            Credentials(email=email, password="sup3rsecret")

    def test_short_password_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Credentials(email="a@b.com", password="short")

    def test_email_is_normalised_by_the_route_not_the_schema(self) -> None:
        # Documents the contract: normalisation happens in the router.
        assert Credentials(email="MiXeD@Example.COM", password="sup3rsecret").email

    @pytest.mark.parametrize("title", ["", " ", "x" * 201])
    def test_bad_titles_are_rejected(self, title: str) -> None:
        with pytest.raises(ValidationError):
            TodoCreate(title=title)

    def test_title_bounds_are_inclusive(self) -> None:
        assert TodoCreate(title="x").title == "x"
        assert len(TodoCreate(title="x" * 200).title) == 200

    def test_empty_update_is_representable_but_flagged_by_the_route(self) -> None:
        assert TodoUpdate().model_dump(exclude_unset=True) == {}

    def test_partial_update_keeps_only_provided_fields(self) -> None:
        assert TodoUpdate(done=True).model_dump(exclude_unset=True) == {"done": True}
