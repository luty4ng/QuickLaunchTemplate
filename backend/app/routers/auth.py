"""Auth endpoints: register, login, logout, whoami.

Session state lives entirely in an httpOnly cookie, so there is no session
table to query on every request and nothing to clean up.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.db.models import User
from app.deps import CurrentUser, SessionDep, api_error
from app.schemas import Credentials, UserOut
from app.security import create_session_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


def _set_session_cookie(response: Response, user_id: str) -> None:
    response.set_cookie(
        key=settings.cookie_name,
        value=create_session_token(user_id),
        max_age=settings.jwt_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(payload: Credentials, response: Response, session: SessionDep) -> User:
    email = payload.email.strip().lower()
    user = User(email=email, password_hash=hash_password(payload.password))
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        # The unique index on users.email is the real arbiter; this catches it.
        raise api_error(
            "email_taken", "That email is already registered.", status.HTTP_409_CONFLICT
        ) from None
    await session.refresh(user)
    _set_session_cookie(response, user.id)
    return user


@router.post("/login", response_model=UserOut)
async def login(payload: Credentials, response: Response, session: SessionDep) -> User:
    email = payload.email.strip().lower()
    user = await session.scalar(select(User).where(User.email == email))
    # Same 401 whether the email is unknown or the password is wrong: no user
    # enumeration through response codes.
    if user is None or not verify_password(payload.password, user.password_hash):
        raise api_error(
            "invalid_credentials", "Email or password is incorrect.", status.HTTP_401_UNAUTHORIZED
        )
    _set_session_cookie(response, user.id)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    ql_session: Annotated[str | None, Cookie(alias=settings.cookie_name)] = None,
) -> Response:
    response.delete_cookie(settings.cookie_name, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    return user
