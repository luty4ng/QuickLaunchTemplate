"""Todo 表。

放在功能包里而不是骨架的 `app/db/models.py`：迁移到新项目时整个 `features/todos/`
会被删掉，表定义自然跟着走，不会在骨架里留一张没人用的表。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, new_id, utcnow
from app.db.models import User


class Todo(Base):
    __tablename__ = "todos"
    # The single hot query is "my todos, newest first" -> one composite index.
    __table_args__ = (Index("ix_todos_user_created", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    done: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Timestamps are generated in Python rather than by the database: sqlite's
    # CURRENT_TIMESTAMP only has second resolution, which would make "newest
    # first" ambiguous for rows created in the same second.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    # 单向关系：待办知道"我属于谁"，而 `User` **不认识** Todo——
    # 这就是骨架与业务的边界在 ORM 上的落点（删掉这个功能包，users 表毫发无损）。
    user: Mapped[User] = relationship()
