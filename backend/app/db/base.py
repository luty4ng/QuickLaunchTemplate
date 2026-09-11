"""Declarative base plus the small helpers every model needs."""

from __future__ import annotations

import os
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    """Time-ordered, collision-free string id (RFC 9562 layout, UUIDv7-like).

    The first 48 bits are a millisecond timestamp, so ids sort in creation
    order. That matters: it makes `ORDER BY created_at DESC, id DESC` stable
    even when two rows land in the same clock tick, and it keeps b-tree inserts
    append-mostly instead of scattering them like uuid4 does.
    """
    millis = int(time.time() * 1000) & ((1 << 48) - 1)
    randomness = int.from_bytes(os.urandom(10), "big")
    value = (millis << 80) | (0x7 << 76) | (randomness & ((1 << 76) - 1))
    return f"{value:032x}"


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_uuid() -> str:
    return uuid.uuid4().hex
