"""The isolation gate.

This file exists to fail loudly if tenant separation ever regresses: user B's
session must behave as if user A's rows do not exist at all. A regression here
is a security incident, not a bug.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import new_client, register

pytestmark = pytest.mark.integration


@pytest.fixture
def two_users(client: TestClient) -> tuple[TestClient, TestClient]:
    """Two independent sessions, each with its own cookie jar."""
    alice = new_client(client)
    bob = new_client(client)
    register(alice, "alice@example.com")
    register(bob, "bob@example.com")
    return alice, bob


def test_lists_are_scoped_to_the_owner(two_users: tuple[TestClient, TestClient]) -> None:
    alice, bob = two_users
    alice.post("/api/todos", json={"title": "alice secret"})
    bob.post("/api/todos", json={"title": "bob secret"})

    alice_titles = [t["title"] for t in alice.get("/api/todos").json()]
    bob_titles = [t["title"] for t in bob.get("/api/todos").json()]

    assert alice_titles == ["alice secret"]
    assert bob_titles == ["bob secret"]


def test_reading_someone_elses_todo_is_404_not_403(two_users: tuple[TestClient, TestClient]) -> None:
    alice, bob = two_users
    alice_todo = alice.post("/api/todos", json={"title": "alice secret"}).json()

    # Bob must not be able to tell "forbidden" from "does not exist".
    response = bob.patch(f"/api/todos/{alice_todo['id']}", json={"done": True})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"

    # ...and the row must be untouched.
    assert alice.get("/api/todos").json()[0]["done"] is False


def test_updating_someone_elses_todo_cannot_leak_state(two_users: tuple[TestClient, TestClient]) -> None:
    alice, bob = two_users
    alice_todo = alice.post("/api/todos", json={"title": "keep me"}).json()

    bob.patch(f"/api/todos/{alice_todo['id']}", json={"title": "hijacked"})
    assert alice.get("/api/todos").json()[0]["title"] == "keep me"


def test_deleting_someone_elses_todo_is_404_and_does_not_delete(
    two_users: tuple[TestClient, TestClient],
) -> None:
    alice, bob = two_users
    alice_todo = alice.post("/api/todos", json={"title": "survive"}).json()

    assert bob.delete(f"/api/todos/{alice_todo['id']}").status_code == 404
    assert len(alice.get("/api/todos").json()) == 1


def test_unknown_ids_and_foreign_ids_answer_identically(two_users: tuple[TestClient, TestClient]) -> None:
    alice, bob = two_users
    alice_todo = alice.post("/api/todos", json={"title": "alice secret"}).json()

    foreign = bob.delete(f"/api/todos/{alice_todo['id']}")
    unknown = bob.delete("/api/todos/does-not-exist-at-all")
    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json() == unknown.json()
