"""End-to-end HTTP behaviour against a migrated database: the skeleton's contract.

Locally these run on sqlite for speed; in CI the same file runs against the
`postgres:16` service container, which is what production uses. The assertions
never touch sqlite-specific behaviour.

Nothing here mentions a business resource: health, sessions and the error
envelope are what the template ships, so this file survives a migration untested.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import register

pytestmark = pytest.mark.integration

EMAIL = "flow@example.com"
PASSWORD = "sup3rsecret"


class TestHealth:
    def test_health_reports_database_up(self, client: TestClient) -> None:
        response = client.get("/api/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["database"] == "up"
        assert body["version"]

    def test_liveness_needs_no_database(self, client: TestClient) -> None:
        assert client.get("/healthz").json() == {"status": "ok"}


class TestAuthFlow:
    def test_register_sets_httponly_cookie_and_returns_user(self, anon: TestClient) -> None:
        response = anon.post("/api/auth/register", json={"email": EMAIL, "password": PASSWORD})
        assert response.status_code == 201
        body = response.json()
        assert body["email"] == EMAIL
        assert "passwordHash" not in response.text and "password_hash" not in response.text

        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie
        assert "SameSite=lax" in cookie.replace("samesite", "SameSite")

    def test_register_is_case_insensitive_on_email(self, anon: TestClient) -> None:
        anon.post("/api/auth/register", json={"email": "Case@Example.com", "password": PASSWORD})
        assert anon.get("/api/auth/me").json()["email"] == "case@example.com"

    def test_duplicate_email_is_409(self, anon: TestClient) -> None:
        register(anon, "dup@example.com")
        response = anon.post("/api/auth/register", json={"email": "dup@example.com", "password": PASSWORD})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "email_taken"

    def test_login_wrong_password_and_unknown_email_are_indistinguishable(self, anon: TestClient) -> None:
        register(anon, "known@example.com")
        wrong = {"email": "known@example.com", "password": "definitely-not-the-password"}
        ghost = {"email": "ghost@example.com", "password": PASSWORD}
        wrong_password = anon.post("/api/auth/login", json=wrong)
        unknown_email = anon.post("/api/auth/login", json=ghost)
        assert wrong_password.status_code == unknown_email.status_code == 401
        assert wrong_password.json() == unknown_email.json()

    def test_logout_clears_the_session(self, anon: TestClient) -> None:
        register(anon, "session@example.com")
        assert anon.get("/api/auth/me").status_code == 200
        assert anon.post("/api/auth/logout").status_code == 204
        assert anon.get("/api/auth/me").status_code == 401

    def test_login_restores_a_session_after_logout(self, anon: TestClient) -> None:
        register(anon, "back@example.com")
        anon.post("/api/auth/logout")

        login = anon.post("/api/auth/login", json={"email": "back@example.com", "password": PASSWORD})
        assert login.status_code == 200
        assert anon.get("/api/auth/me").status_code == 200

    def test_invalid_body_is_400_with_error_envelope(self, anon: TestClient) -> None:
        response = anon.post("/api/auth/register", json={"email": "nope", "password": "short"})
        assert response.status_code == 422  # FastAPI validation -> client error
        assert "detail" in response.json()

    def test_a_forged_cookie_is_rejected(self, anon: TestClient) -> None:
        anon.cookies.set("ql_session", "eyJhbGciOiJIUzI1NiJ9.forged.signature")
        response = anon.get("/api/auth/me")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthenticated"

    def test_the_session_cookie_name_is_the_one_the_client_sends(self, anon: TestClient) -> None:
        # The name is part of the deployed contract: a browser that is already
        # signed in carries it, so changing it logs everyone out on deploy.
        register(anon, "cookie@example.com")
        assert "ql_session" in anon.cookies
