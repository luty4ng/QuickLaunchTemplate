"""配额：这个用户还能建几条待办。

从 billing 的 router 里搬过来的——它算的是"待办的数量上限"，属于 todos 域；
billing 只是**展示**这个结果（`/api/billing/me` 里的 quota 字段），所以由 billing 反向导入这里。
迁移时如果删掉 todos，billing 里那两个 quota 字段一并删掉即可（`features/billing/router.py` 有注记）。
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.deps import SessionDep
from app.features.todos.models import Todo
from app.features.todos.schemas import QuotaOut
from app.features.todos.settings import get_todo_settings


async def used_todos(session: SessionDep, user_id: str) -> int:
    return int(
        await session.scalar(select(func.count()).select_from(Todo).where(Todo.user_id == user_id)) or 0
    )


def quota_for(plan: str, used: int) -> QuotaOut:
    limit = get_todo_settings().todo_limit_for(plan)
    return QuotaOut(
        plan=plan,
        limit=limit,
        used=used,
        remaining=None if limit is None else max(limit - used, 0),
        can_create=limit is None or used < limit,
    )
