"""Test bootstrap.

Environment variables are set *before* the application is imported so
`get_settings()` (an lru_cache) picks them up.

Isolation model: one migrated sqlite file for the whole session, wiped between
tests. Each test that needs a distinct session builds its own TestClient, which
keeps its own cookie jar.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="quicklaunch-tests-"))
_DB_FILE = _TMP / "test.db"

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_DB_FILE.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-that-is-long-enough-for-hs256"
os.environ["BCRYPT_ROUNDS"] = "4"  # keeps the suite fast
os.environ["AUTO_MIGRATE"] = "true"
os.environ["WEB_DIST"] = str(_TMP / "no-web-dist")

# Billing is "configured" with obvious placeholders so the routes take their real
# path. The gateway itself is replaced with the in-memory fake below, so no test
# touches the network - the same reason these values are not real keys.
os.environ["STRIPE_SECRET_KEY"] = "sk_test_fake_for_tests"
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_fake_for_tests"
os.environ["STRIPE_PRICE_PLUS"] = "price_fake_plus"
os.environ["STRIPE_PRICE_PRO"] = "price_fake_pro"
os.environ["FREE_TODO_LIMIT"] = "10"
os.environ["PLUS_TODO_LIMIT"] = "200"

import asyncio  # noqa: E402

import sqlalchemy as sa  # noqa: E402
from app.billing.fake import FakeGateway  # noqa: E402
from app.billing.router import get_gateway  # noqa: E402
from app.main import app, run_migrations  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

_DATABASE_URL = os.environ["DATABASE_URL"]


@pytest.fixture(scope="session", autouse=True)
def _migrated_database() -> None:
    """Bring the schema up once, independently of whether `client` is used.

    The unit tests never build a TestClient, so without this they would run
    their table cleanup against a database that has no tables - which is exactly
    what happened the first time this suite ran in CI.
    """
    run_migrations()


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


async def _wipe() -> None:
    engine = create_async_engine(_DATABASE_URL)
    async with engine.begin() as connection:
        # billing_events references users, so it goes first.
        await connection.execute(sa.text("DELETE FROM billing_events"))
        await connection.execute(sa.text("DELETE FROM todos"))
        await connection.execute(sa.text("DELETE FROM users"))
    await engine.dispose()


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """Every test starts from an empty database."""
    asyncio.run(_wipe())


@pytest.fixture
def fake_gateway() -> FakeGateway:
    """The provider, in memory.

    A fresh instance per test, so one test's sessions and subscriptions cannot
    leak into the next. This is what keeps the suite offline: no test can reach
    Stripe even by accident, because the gateway it is given has no network code.
    """
    gateway = FakeGateway()
    app.dependency_overrides[get_gateway] = lambda: gateway
    yield gateway
    app.dependency_overrides.pop(get_gateway, None)


@pytest.fixture
def anon(client: TestClient, fake_gateway: FakeGateway) -> TestClient:
    """A client with no session cookie, with billing wired to the fake."""
    client.cookies.clear()
    return client


def new_client(source: TestClient) -> TestClient:
    """A second client (own cookie jar) aimed at the same app."""
    return TestClient(source.app)


def register(client: TestClient, email: str, password: str = "sup3rsecret") -> TestClient:
    response = client.post("/api/auth/register", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    return client
