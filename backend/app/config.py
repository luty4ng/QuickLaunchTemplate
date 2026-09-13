"""Application settings.

Everything is environment driven so the same image runs in CI, on a laptop and
in production without a code change. See `.env.example` for the full list.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "QuickLaunch API"
    app_env: str = "development"

    # Comma separated list of extra browser origins allowed to call the API.
    # The web client is same-origin in production, so normally this stays empty.
    cors_origins: str = ""

    database_url: str = "sqlite+aiosqlite:///./data/app.db"
    auto_migrate: bool = True

    jwt_secret: str = "dev-only-secret-change-me"
    jwt_alg: str = "HS256"
    jwt_ttl_seconds: int = 60 * 60 * 24 * 7

    # bcrypt cost. Tests drop this to 4 so the suite stays fast.
    bcrypt_rounds: int = Field(default=12, ge=4, le=16)
    password_min_length: int = 8

    cookie_name: str = "ql_session"
    # Set to true when the API is served over HTTPS directly (not behind TLS).
    cookie_secure: bool = False

    # Directory holding the built web client. Mounted into the image; the API
    # falls back to "API only" mode when it is missing (useful in tests).
    web_dist: Path = REPO_ROOT / "web" / "dist"
    # Where the desktop update feed lives. Served by this app at
    # /updates/latest.yml, written by the release pipeline on the server.
    updates_file: Path = REPO_ROOT / "updates" / "latest.yml"

    # --- Plans and quotas -------------------------------------------------
    # Todo limits per plan. `None` means unlimited; keep it out of the JSON
    # surface and treat it as "no check" in code.
    free_todo_limit: int = Field(default=10, ge=0)
    plus_todo_limit: int = Field(default=200, ge=0)

    # --- Stripe -----------------------------------------------------------
    # Empty everywhere except the deployment (and never in the repository). The
    # billing endpoints answer 503 when these are missing rather than pretending
    # to work, and CI exercises the same code path with an injected fake gateway.
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_plus: str = ""
    stripe_price_pro: str = ""
    # Point the provider at something other than api.stripe.com. Only used to
    # exercise the real HTTP code path against scripts/fake_stripe.py; empty means
    # the official API.
    stripe_api_base: str = ""
    # Where Stripe sends the browser back after Checkout / the customer portal.
    # Relative paths are resolved against the request's own origin when unset.
    stripe_success_path: str = "/?billing=success"
    stripe_cancel_path: str = "/?billing=cancelled"
    # Tolerance for webhook signature timestamps. Stripe's default is 300s; the
    # server's clock must be roughly right or every event is rejected.
    stripe_webhook_tolerance: int = Field(default=300, ge=30)

    @property
    def stripe_enabled(self) -> bool:
        """True only when the API can actually talk to Stripe."""
        return bool(self.stripe_secret_key and self.stripe_price_plus and self.stripe_price_pro)

    @property
    def stripe_uses_official_api(self) -> bool:
        return not self.stripe_api_base

    def price_id_for(self, plan: str) -> str:
        return {"plus": self.stripe_price_plus, "pro": self.stripe_price_pro}.get(plan, "")

    def plan_for_price(self, price_id: str) -> str | None:
        """Map a Stripe price back to a plan; None when it is not ours."""
        if price_id and price_id == self.stripe_price_plus:
            return "plus"
        if price_id and price_id == self.stripe_price_pro:
            return "pro"
        return None

    def todo_limit_for(self, plan: str) -> int | None:
        """None means unlimited."""
        if plan == "plus":
            return self.plus_todo_limit
        if plan == "pro":
            return None
        return self.free_todo_limit

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def database_kind(self) -> str:
        return self.database_url.split(":", 1)[0].split("+", 1)[0]

    def prepare_local_dirs(self) -> None:
        """Make sure the sqlite parent directory exists before we connect."""
        if self.database_kind != "sqlite":
            return
        path = self.database_url.split("///", 1)[-1]
        if path and path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
