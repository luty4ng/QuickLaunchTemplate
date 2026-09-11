"""Async engine + session factory.

`pool_pre_ping` matters in containers: Postgres restarts (deploys, failovers)
otherwise hand out dead connections and every first request 500s.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

_settings = get_settings()
_settings.prepare_local_dirs()

_connect_args = {"check_same_thread": False} if _settings.database_kind == "sqlite" else {}

engine = create_async_engine(
    _settings.database_url,
    echo=False,
    pool_pre_ping=True,
    future=True,
    connect_args=_connect_args,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
