"""待办的请求/响应契约，以及"还能建几条"这个配额契约。

`QuotaOut` 跟着 todos 走（因为配额是"待办能建多少"这件事），billing 需要展示它时
从**这里**导入——方向是 billing → todos，反过来骨架不会认识任何一个。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

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


class QuotaOut(BaseModel):
    """What the plan allows, how much is used, and whether one more fits."""

    plan: str
    limit: int | None
    used: int
    remaining: int | None
    can_create: bool
