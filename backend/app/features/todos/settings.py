"""待办功能的设置：每个档位能建多少条。

和环境变量同名（`FREE_TODO_LIMIT` / `PLUS_TODO_LIMIT`），所以现有 `.env` 不用改。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TodoSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # `None` 表示不限量——保持它不出现在 JSON 里，代码里当"不检查"处理。
    free_todo_limit: int = Field(default=10, ge=0)
    plus_todo_limit: int = Field(default=200, ge=0)

    def todo_limit_for(self, plan: str) -> int | None:
        """None means unlimited."""
        if plan == "plus":
            return self.plus_todo_limit
        if plan == "pro":
            return None
        return self.free_todo_limit


@lru_cache(maxsize=1)
def get_todo_settings() -> TodoSettings:
    return TodoSettings()
