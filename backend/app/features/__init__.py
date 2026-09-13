"""业务功能（features）：模板的"示例业务"全都住在这里。

骨架（`app/` 下其余部分：config、db、deps、security、routers/auth|health|updates、main）
**不认识**这些包里的任何东西——它只知道这个文件导出的注册表。这条边界由
`tests/unit/test_layering.py` 守着：skeleton 里出现 `app.features` 的导入就会红。

迁移到新项目时：

    rm -rf backend/app/features/todos backend/app/features/billing
    # 然后在 FEATURES 里放你自己的功能包

删完骨架仍然能跑（`/api/health`、注册登录、更新源都在），只是没有业务接口了。

加一个功能包的最小形态：

    app/features/mine/__init__.py   FEATURE = Feature(name="mine", router=router)
    app/features/mine/router.py     router = APIRouter(prefix="/mine", tags=["mine"])
    app/features/mine/models.py     Base 子类（alembic 会看到它，见 migrations/env.py）

然后在下面 FEATURES 里加一项即可——不需要动 `main.py`。
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, FastAPI

# 前缀统一在这里给，功能包自己的 router 只写自己的那一段路径。
API_PREFIX = "/api"


@dataclass(frozen=True)
class Feature:
    """一个功能包对骨架的全部交代：名字 + 路由（可无，纯后台功能就没有路由）。"""

    name: str
    router: APIRouter | None = None


# 导入即注册：feature 的 models 会挂到 Base.metadata 上（alembic 靠这个看到表）。
from app.features import billing, todos  # noqa: E402  （必须在上面的定义之后导入）

FEATURES: tuple[Feature, ...] = (todos.FEATURE, billing.FEATURE)


def load(app: FastAPI) -> None:
    """把每个功能包的路由挂到 API 前缀下。

    骨架不需要知道具体是什么功能——这是"业务与骨架分层"在代码里的落点：
    `main.py` 里没有一行提到 todos 或 billing。
    """
    for feature in FEATURES:
        if feature.router is not None:
            app.include_router(feature.router, prefix=API_PREFIX)
