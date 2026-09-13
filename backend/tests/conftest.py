"""Test bootstrap.

Environment variables are set *before* the application is imported so
`get_settings()` (an lru_cache) picks them up.

Isolation model: one migrated sqlite file for the whole session, wiped between
tests. Each test that needs a distinct session builds its own TestClient, which
keeps its own cookie jar.

**This file knows no business.** Nothing here imports `app.features.*`: the
skeleton suite (health, auth, sessions, update feed, layering) has to stay green
on a checkout where the example features have been deleted. The example features
bring their own fixtures in `tests/*/features/conftest.py`, and their own settings
in the block below - delete that block with them.
"""

from __future__ import annotations

import os
import sys
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

# --- settings of the example features (delete with app/features/) ------------
# Billing is "configured" with obvious placeholders so the routes take their real
# path. The gateway itself is replaced with the in-memory fake in
# `tests/integration/features/conftest.py`, so no test touches the network - the
# same reason these values are not real keys. They have to be set here rather than
# there because `app/features/billing/router.py` reads its settings at import time
# and the app is imported a few lines below.
os.environ["STRIPE_SECRET_KEY"] = "sk_test_fake_for_tests"
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_fake_for_tests"
os.environ["STRIPE_PRICE_PLUS"] = "price_fake_plus"
os.environ["STRIPE_PRICE_PRO"] = "price_fake_pro"
os.environ["FREE_TODO_LIMIT"] = "10"
os.environ["PLUS_TODO_LIMIT"] = "200"
# ----------------------------------------------------------------------------

# `scripts/` holds check_version.py, make_feed.py and the shared fake-provider
# core - the delivery path that these tests pin. Added explicitly rather than
# picked up as a side effect of whichever import happened to reach it first.
_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import asyncio  # noqa: E402

import sqlalchemy as sa  # noqa: E402
from app.db.base import Base  # noqa: E402
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
    """Empty every table the installed features declared.

    Deliberately generic: the table list comes from `Base.metadata`, so a new
    feature is covered without touching this file (and a deleted one leaves
    nothing to clean up). Reversed order keeps foreign keys happy on postgres.
    """
    engine = create_async_engine(_DATABASE_URL)
    async with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            await connection.execute(sa.text(f'DELETE FROM "{table.name}"'))
    await engine.dispose()


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """Every test starts from an empty database."""
    asyncio.run(_wipe())


@pytest.fixture
def anon(client: TestClient) -> TestClient:
    """A client with no session cookie.

    Business tests that need the payment provider fake ask for `fake_gateway` as
    well (it is autouse inside `tests/integration/features/`).
    """
    client.cookies.clear()
    return client


def new_client(source: TestClient) -> TestClient:
    """A second client (own cookie jar) aimed at the same app."""
    return TestClient(source.app)


def register(client: TestClient, email: str, password: str = "sup3rsecret") -> TestClient:
    response = client.post("/api/auth/register", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    return client
