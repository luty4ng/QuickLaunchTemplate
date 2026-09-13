"""部署门禁自己也分层：骨架检查不许碰示例业务的路径。

`scripts/smoke.py` 在真实部署上跑，所以它不属于任何一套单元测试——但它的
"骨架组"（health / SPA / 会话 / 更新源）必须能在换业务之后原样使用，而这条
边界没有别的东西守着：迁移预演删掉业务后只跑 pytest，不会去跑一个需要活服务器的门禁。

所以这里用 AST 做静态检查：骨架组那几个函数里出现 `/api/todos`、`/api/billing`
这类业务路径就直接失败。同一类保护见 `tests/unit/test_layering.py`。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SMOKE = Path(__file__).resolve().parents[3] / "scripts" / "smoke.py"

# 骨架组的检查函数：不碰任何业务资源，迁移后原样可用。
SKELETON_FUNCTIONS = ("check_health", "check_spa", "check_session", "check_update_feed")
# 示例业务组的检查函数：把这两个前缀当载体（它们当然会提到）。
BUSINESS_MARKERS = ("/api/todos", "/api/billing")


def function_sources() -> dict[str, str]:
    """每个函数名 -> 它的源码片段（含默认值），用于静态检查。"""
    text = SMOKE.read_text(encoding="utf-8")
    tree = ast.parse(text)
    lines = text.splitlines()
    sources: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            sources[node.name] = "\n".join(lines[node.lineno - 1 : node.end_lineno])
    return sources


def test_the_smoke_script_is_where_we_think_it_is() -> None:
    sources = function_sources()
    missing = [name for name in SKELETON_FUNCTIONS if name not in sources]
    assert not missing, f"smoke.py 里找不到这些骨架检查：{missing}（改名了就把测试一起改）"


def test_skeleton_checks_never_touch_a_business_path() -> None:
    sources = function_sources()
    offenders = [
        f"{name} -> {marker}"
        for name in SKELETON_FUNCTIONS
        for marker in BUSINESS_MARKERS
        if marker in sources[name]
    ]
    assert not offenders, (
        "smoke.py 的骨架检查里出现了业务路径：\n  "
        + "\n  ".join(offenders)
        + "\n\n正确做法：把这类断言放到示例业务组（check_journey / check_isolation / check_billing），"
        "换业务时它们会被替换掉，骨架组要能原样留着。"
    )


def test_the_business_group_still_needs_a_way_out() -> None:
    """换业务的人必须有一条"先只跑骨架"的路，否则门禁会在迁移期间一直红。"""
    text = SMOKE.read_text(encoding="utf-8")
    assert "--skip-business" in text, "smoke.py 必须提供 --skip-business"
    assert "args.skip_business" in text, "--skip-business 解析了但没有被用上"
