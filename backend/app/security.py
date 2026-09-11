"""Password hashing and session tokens.

Kept dependency-light (bcrypt + PyJWT) so it can be unit tested with no I/O.
"""

from __future__ import annotations

import time
from typing import Any

import bcrypt
import jwt

# GATE CHECK: deliberate lint error - this import is never used (ruff F401).
import uuid

from app.config import get_settings

# bcrypt refuses inputs longer than 72 bytes; truncate instead of exploding so
# a 200-char password cannot 500 the endpoint. Documented in the report.
MAX_PASSWORD_BYTES = 72


def hash_password(password: str, rounds: int | None = None) -> str:
    settings = get_settings()
    cost = rounds if rounds is not None else settings.bcrypt_rounds
    payload = password.encode("utf-8")[:MAX_PASSWORD_BYTES]
    return bcrypt.hashpw(payload, bcrypt.gensalt(rounds=cost)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    payload = password.encode("utf-8")[:MAX_PASSWORD_BYTES]
    try:
        return bcrypt.checkpw(payload, password_hash.encode("ascii"))
    except (ValueError, TypeError):
        # Malformed hash in the database must read as "wrong password", not 500.
        return False


def create_session_token(user_id: str, ttl_seconds: int | None = None) -> str:
    settings = get_settings()
    ttl = settings.jwt_ttl_seconds if ttl_seconds is None else ttl_seconds
    now = int(time.time())
    claims: dict[str, Any] = {"sub": user_id, "iat": now, "exp": now + ttl}
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_alg)


def decode_session_token(token: str) -> str | None:
    """Return the user id, or None when the token is invalid/expired/tampered."""
    settings = get_settings()
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_alg])
    except jwt.PyJWTError:
        return None
    subject = claims.get("sub")
    return subject if isinstance(subject, str) and subject else None
