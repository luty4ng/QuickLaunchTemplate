"""骨架不许认识示例业务。

这条边界是"一键迁移"能不能成立的关键：迁移时 `rm -rf app/features/todos app/features/billing`
（连同 `tests/*/features/`）之后，骨架（认证、会话、健康检查、更新源、错误处理、数据库管道）
必须仍然能跑，新项目只要放自己的功能包即可。

所以这个测试用 AST 扫一遍骨架文件，出现任何 `app.features...` 的导入就失败。
不用人盯代码评审，也不靠"记得别这么写"——那正是模板最容易腐化的地方。

注意方向是**单向**的：功能包可以随便导入骨架（`app.db.base`、`app.deps`、`app.config`…），
只有骨架反向导入功能包才被禁止。

功能包的名字**从磁盘读**，不写死：换业务之后这个文件不需要改。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

APP = Path(__file__).resolve().parents[2] / "app"
FEATURES_DIR = APP / "features"

# 骨架文件：app/ 下除 features/ 之外的全部 Python 文件。
SKELETON_GLOBS = ("*.py", "db/*.py", "routers/*.py")


def skeleton_files() -> list[Path]:
    files: list[Path] = []
    for pattern in SKELETON_GLOBS:
        files.extend(sorted(APP.glob(pattern)))
    assert files, "没有找到骨架文件——路径判断错了，测试会永远通过"
    return files


def feature_names() -> list[str]:
    """装了什么功能包，就问磁盘——注册表和测试都以此为准。"""
    return sorted(
        path.name
        for path in FEATURES_DIR.iterdir()
        if path.is_dir() and not path.name.startswith("_") and (path / "__init__.py").exists()
    )


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_the_skeleton_does_not_import_any_feature() -> None:
    offenders: list[str] = []
    for path in skeleton_files():
        for module in sorted(imported_modules(path)):
            if module == "app.features" or module.startswith("app.features."):
                offenders.append(f"{path.relative_to(APP.parent)} -> {module}")

    assert not offenders, (
        "骨架文件导入了功能包，迁移时删掉 features/ 就会崩：\n  "
        + "\n  ".join(offenders)
        + "\n\n正确做法：把功能包需要的东西放在骨架（app/config.py、app/deps.py、app/db/…），"
        "由功能包单向导入；功能对骨架的要求通过 app/features/__init__.py 的注册表表达。"
    )


def test_the_registry_is_the_only_link() -> None:
    """`main.py` 只通过注册表挂载业务——否则它就是在认识具体功能。"""
    main = (APP / "main.py").read_text(encoding="utf-8")
    assert "features.load(app)" in main, "main.py 应当通过 features.load(app) 挂载业务路由"
    for name in feature_names():
        assert f"routers import {name}" not in main, f"main.py 直接导入了 {name} 路由"
        assert f"import {name}" not in main, f"main.py 直接导入了 {name}"


def test_every_feature_package_is_registered_exactly_once() -> None:
    """磁盘上的功能包与注册表必须一一对应——多一个不挂载，少一个就崩。"""
    from app.features import FEATURES

    registered = [feature.name for feature in FEATURES]
    assert len(registered) == len(set(registered)), f"注册表里有重复项：{registered}"
    assert set(registered) == set(feature_names()), (
        f"注册表 {sorted(registered)} 与 app/features/ 下的目录 {feature_names()} 不一致；"
        "加功能包要在 app/features/__init__.py 的 FEATURES 里加一项"
    )
    for feature in FEATURES:
        assert feature.name, "feature 必须有名字"
