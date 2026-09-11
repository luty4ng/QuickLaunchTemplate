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
