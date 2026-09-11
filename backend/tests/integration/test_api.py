"""End-to-end HTTP behaviour against a migrated database.

Locally these run on sqlite for speed; in CI the same file runs against the
`postgres:16` service container, which is what production uses. The assertions
never touch sqlite-specific behaviour.
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


class TestTodoCrud:
    def test_full_lifecycle(self, anon: TestClient) -> None:
        register(anon, "crud@example.com")

        assert anon.get("/api/todos").json() == []

        created = anon.post("/api/todos", json={"title": "  ship the pipeline  "})
        assert created.status_code == 201
        todo = created.json()
        assert todo["title"] == "ship the pipeline"  # trimmed
        assert todo["done"] is False

        renamed = anon.patch(f"/api/todos/{todo['id']}", json={"title": "ship it"})
        assert renamed.status_code == 200
        assert renamed.json()["title"] == "ship it"

        completed = anon.patch(f"/api/todos/{todo['id']}", json={"done": True})
        assert completed.json()["done"] is True
        assert completed.json()["title"] == "ship it"  # untouched

        assert [t["id"] for t in anon.get("/api/todos").json()] == [todo["id"]]

        assert anon.delete(f"/api/todos/{todo['id']}").status_code == 204
        assert anon.get("/api/todos").json() == []
        assert anon.delete(f"/api/todos/{todo['id']}").status_code == 404

    def test_todos_are_newest_first(self, anon: TestClient) -> None:
        register(anon, "order@example.com")
        for title in ("first", "second", "third"):
            anon.post("/api/todos", json={"title": title})
        assert [t["title"] for t in anon.get("/api/todos").json()] == ["third", "second", "first"]

    def test_empty_update_is_rejected(self, anon: TestClient) -> None:
        register(anon, "empty@example.com")
        todo_id = anon.post("/api/todos", json={"title": "a"}).json()["id"]
        response = anon.patch(f"/api/todos/{todo_id}", json={})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "empty_update"

    def test_title_limits_are_enforced(self, anon: TestClient) -> None:
        register(anon, "limits@example.com")
        assert anon.post("/api/todos", json={"title": ""}).status_code == 422
        assert anon.post("/api/todos", json={"title": "x" * 201}).status_code == 422
        assert anon.post("/api/todos", json={"title": "ok"}).status_code == 201

    @pytest.mark.parametrize(
        ("method", "path", "payload"),
        [
            ("get", "/api/todos", None),
            ("post", "/api/todos", {"title": "sneaky"}),
            ("patch", "/api/todos/whatever", {"done": True}),
            ("delete", "/api/todos/whatever", None),
        ],
    )
    def test_all_todo_routes_require_a_session(
        self, anon: TestClient, method: str, path: str, payload: dict[str, object] | None
    ) -> None:
        response = getattr(anon, method)(path, json=payload) if payload else getattr(anon, method)(path)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthenticated"

    def test_forged_cookie_is_rejected(self, anon: TestClient) -> None:
        anon.cookies.set("ql_session", "eyJhbGciOiJIUzI1NiJ9.forged.signature")
        assert anon.get("/api/todos").status_code == 401
