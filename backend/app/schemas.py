"""骨架的请求/响应契约：认证、错误、健康检查。

业务契约跟着功能包走——待办的模型在 `app/features/todos/schemas.py`，
计费的在 `app/features/billing/schemas.py`。骨架的 schema 里出现 Todo 字段，
就说明骨架又开始认识示例业务了（`tests/unit/test_layering.py` 会拦）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.config import get_settings

_settings = get_settings()


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=_settings.password_min_length, max_length=200)


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
