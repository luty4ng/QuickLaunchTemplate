"""Example business: the todo resource over HTTP.

Replaced with your own resource's tests when you migrate; the skeleton's own
HTTP contract lives in `tests/integration/test_api.py` and does not mention todos.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import register

pytestmark = pytest.mark.integration


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
