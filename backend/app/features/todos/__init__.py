"""待办清单：模板的示例业务功能包。

它演示一个功能包应该长什么样——自己的模型、自己的接口契约、自己的设置、自己的路由，
对外只通过 `FEATURE` 交代一句"我叫 todos，路由是这个"。骨架不认识它。
"""

from __future__ import annotations

from app.features import Feature
from app.features.todos.router import router

FEATURE = Feature(name="todos", router=router)

__all__ = ["FEATURE", "router"]
