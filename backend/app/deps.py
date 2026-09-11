"""Shared FastAPI dependencies and the uniform error shape.

Every failure the API produces on purpose travels as `ApiError`, which one
handler renders as `{"error": {"code", "message"}}`. Clients therefore have a
single shape to parse, and FastAPI's own 422 body stays untouched for
schema-validation failures.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Cookie, Depends, FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import User
from app.db.session import get_session
from app.security import decode_session_token

SessionDep = Annotated[AsyncSession, Depends(get_session)]
settings = get_settings()


class ApiError(Exception):
    """A deliberate, client-visible failure."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def api_error(code: str, message: str, status_code: int) -> ApiError:
    return ApiError(code, message, status_code)


def install_error_handler(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _handle(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )


async def current_user(
    session: SessionDep,
    session_cookie: Annotated[str | None, Cookie(alias=settings.cookie_name)] = None,
) -> User:
    if not session_cookie:
        raise api_error("unauthenticated", "Sign in to continue.", status.HTTP_401_UNAUTHORIZED)
    user_id = decode_session_token(session_cookie)
    if not user_id:
        raise api_error("unauthenticated", "Session expired. Sign in again.", status.HTTP_401_UNAUTHORIZED)
    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None:
        # Signature is fine but the account is gone: that is a signed-out state.
        raise api_error("unauthenticated", "Session expired. Sign in again.", status.HTTP_401_UNAUTHORIZED)
    return user


CurrentUser = Annotated[User, Depends(current_user)]
