"""Request/response contracts. Every inbound body is validated here."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.config import get_settings

_settings = get_settings()

MIN_TITLE = 1
MAX_TITLE = 200


def _clean_title(value: str) -> str:
    """Trim first, then judge length, so "   " is rejected as empty."""
    cleaned = value.strip()
    if len(cleaned) < MIN_TITLE:
        raise ValueError("title must not be blank")
    if len(cleaned) > MAX_TITLE:
        raise ValueError(f"title must be at most {MAX_TITLE} characters")
    return cleaned


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=_settings.password_min_length, max_length=200)


class TodoCreate(BaseModel):
    title: str

    _check_title = field_validator("title")(_clean_title)


class TodoUpdate(BaseModel):
    # Both optional, but an empty body is not a valid update (the route says so).
    title: str | None = None
    done: bool | None = None

    @field_validator("title")
    @classmethod
    def _check_optional_title(cls, value: str | None) -> str | None:
        return None if value is None else _clean_title(value)


class TodoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    done: bool
    created_at: datetime
    updated_at: datetime


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class HealthResponse(BaseModel):
    status: str
    database: str
    version: str
