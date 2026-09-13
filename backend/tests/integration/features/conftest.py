"""Fixtures for the example features' tests.

Everything under `tests/*/features/` belongs to `app/features/`: migrating means
deleting both directories together and writing your own tests next to your own
features. Nothing outside these directories imports a feature package, which is
what keeps the skeleton suite green on a checkout with no business installed.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from app.features.billing.fake import FakeGateway
from app.features.billing.router import get_gateway
from app.main import app


@pytest.fixture(autouse=True)
def fake_gateway() -> Iterator[FakeGateway]:
    """The payment provider, in memory, for every test in this directory.

    Autouse, so a test that only writes a todo still cannot reach the network by
    accident; a test that asks for `fake_gateway` gets the same instance the app
    is using. A fresh instance per test, so one test's sessions and subscriptions
    cannot leak into the next - this is what keeps the suite offline, because the
    gateway it is given has no network code.

    `base_url` is set here rather than left at the default, so a test asserts
    against a host it chose instead of one that happens to be hardcoded in the
    fake - the default is a reachable localhost address because the deployment
    smoke test derives its control endpoint from the returned url.
    """
    gateway = FakeGateway(base_url="https://checkout.example.test")
    app.dependency_overrides[get_gateway] = lambda: gateway
    yield gateway
    app.dependency_overrides.pop(get_gateway, None)
