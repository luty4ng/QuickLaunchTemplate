"""骨架的表：目前只有"人是谁"。

业务表住在各自的功能包里（`app/features/*/models.py`），它们从这里导入 `Base`。
迁移到新项目时这个文件基本不用动。

**已知的一处妥协**：`users` 上还留着 `plan` 与 Stripe 的几列——它们是 billing 功能的状态，
理想位置是 billing 自己的表。它们之所以还在这里，是因为拆表要先做一次数据迁移
（把 `plan`/`stripe_*` 搬到 billing 的表里再删列），那件事单独做，见 `MIGRATION.md`。
骨架代码**不读这些列**（`plan` 只作为列默认值出现），所以删掉 features/ 之后它们只是几列
没人碰的字段，不会把骨架和示例业务绑在一起。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, new_id, utcnow


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    # Stored lower-cased; uniqueness is what makes login unambiguous.
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    # --- 以下属于示例业务 billing（待搬到它自己的表，见文件头说明）--------------
    # 用字面量而不是从 billing 导入常量：骨架不许依赖功能包（tests/unit/test_layering.py 守着）。
    plan: Mapped[str] = mapped_column(String(16), default="free", nullable=False)

    stripe_customer_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    # Stripe's own vocabulary (active, trialing, past_due, canceled, ...). Stored
    # verbatim so no mapping layer can lose information.
    subscription_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    subscription_price_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
