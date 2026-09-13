#!/usr/bin/env python3
"""迁移预演：把示例业务整块删掉，验证骨架仍然自洽。

模板最核心的承诺是"换项目 = 删掉那几个目录 + 改注册表一行"。这句话很容易在
后续开发里悄悄失效——某个骨架文件 import 了 todos，某个测试 fixture 依赖 billing，
平时全绿，直到你真的迁移时才发现。所以它需要一条能反复执行的验证命令，而不是靠评审：

    python scripts/rehearse_migration.py            # 完整预演（后端 + 前端）
    python scripts/rehearse_migration.py --skip-web # 只跑后端（CI 用这个）
    python scripts/rehearse_migration.py --keep     # 保留副本，方便进去看

它做的事：复制一份仓库到临时目录 → 删掉示例业务（后端 + 前端 + 它们的测试）→
在每个注册表里清空 FEATURES → 在副本里跑骨架自己的测试。副本里的依赖用符号链接
（Windows 上是 junction）指回原仓库，不重新安装。

零业务状态下必须全绿的是：
  * `pytest tests/unit` / `pytest tests/integration`（骨架契约 + 分层守卫）
  * 前端 `lint` / `typecheck` / `test` / `build`（当 --skip-web 未给时）
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 示例业务：三个后端目录 + 两个前端目录 + 两侧各自的测试。
BUSINESS_PATHS = (
    "backend/app/features/todos",
    "backend/app/features/billing",
    "backend/tests/unit/features",
    "backend/tests/integration/features",
    "web/src/features/todos",
    "web/src/features/billing",
)

# 注册表里唯一需要改的地方（各一条 import 一行 FEATURES）。
REGISTRY_EDITS: dict[str, tuple[tuple[str, str], ...]] = {
    "backend/app/features/__init__.py": (
        ("from app.features import billing, todos  # noqa: E402  （必须在上面的定义之后导入）\n\n", ""),
        (
            "FEATURES: tuple[Feature, ...] = (todos.FEATURE, billing.FEATURE)",
            "FEATURES: tuple[Feature, ...] = ()",
        ),
    ),
    "web/src/features/index.ts": (
        ("import { FEATURE as billing } from './billing'\n", ""),
        ("import { FEATURE as todos } from './todos'\n", ""),
        (
            "export const FEATURES: readonly Feature[] = [billing, todos]",
            "export const FEATURES: readonly Feature[] = []",
        ),
    ),
}

SKIP_WHEN_COPYING = shutil.ignore_patterns(
    ".git", ".venv", "node_modules", "dist", ".cache", ".ruff_cache", "__pycache__", ".npm-cache"
)


def link_dependency(source: Path, destination: Path) -> bool:
    """Point the copy at the original's installed dependencies."""
    if not source.exists():
        return False
    if destination.exists() or destination.is_symlink():
        return True
    try:
        os.symlink(source, destination, target_is_directory=True)
        return True
    except OSError:
        pass
    if sys.platform == "win32":
        # A junction needs no developer mode and no admin rights.
        made = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(destination), str(source)],
            capture_output=True,
            text=True,
        )
        return made.returncode == 0
    return False


def prepare_copy(target: Path) -> None:
    print(f"1. 复制仓库 -> {target}")
    shutil.copytree(ROOT, target, ignore=SKIP_WHEN_COPYING, symlinks=True, dirs_exist_ok=True)
    for relative in ("web/node_modules",):
        source = ROOT / relative
        if link_dependency(source, target / relative):
            print(f"   链接依赖 {relative} -> 原仓库（不重新安装）")


def strip_business(target: Path) -> None:
    print("\n2. 删掉示例业务与其测试")
    for relative in BUSINESS_PATHS:
        path = target / relative
        if path.exists():
            shutil.rmtree(path)
            print(f"   删除 {relative}")

    print("\n3. 只改注册表")
    for relative, edits in REGISTRY_EDITS.items():
        path = target / relative
        text = original = path.read_text(encoding="utf-8")
        for before, after in edits:
            if before not in text:
                raise SystemExit(
                    f"::error::{relative}: 找不到要替换的内容 {before!r}（注册表变了，更新本脚本）"
                )
            text = text.replace(before, after)
        if text == original:
            raise SystemExit(f"::error::{relative}: 没有产生任何改动——预演无效")
        path.write_text(text, encoding="utf-8")
        print(f"   改写 {relative}")


def run(label: str, command: list[str], cwd: Path) -> bool:
    result = subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    lines = [line for line in (result.stdout or "").splitlines() if line.strip()]
    print(f"   {'OK  ' if result.returncode == 0 else 'FAIL'} {label}: {lines[-1] if lines else '(无输出)'}")
    if result.returncode != 0:
        print("\n".join(f"      {line}" for line in (result.stdout or "").splitlines()[-25:]))
        print("\n".join(f"      {line}" for line in (result.stderr or "").splitlines()[-15:]))
    return result.returncode == 0


def verify(target: Path, skip_web: bool, skip_backend: bool) -> bool:
    python = sys.executable
    npm = shutil.which("npm")
    ok = True

    if skip_backend:
        print("\n4. 骨架自己的测试（后端）：按 --skip-backend 跳过")
    else:
        print("\n4. 骨架自己的测试（后端）")
        ok &= run("pytest tests/unit", [python, "-m", "pytest", "tests/unit", "-q"], target / "backend")
        ok &= run(
            "pytest tests/integration",
            [python, "-m", "pytest", "tests/integration", "-q"],
            target / "backend",
        )

    if skip_web:
        print("\n5. 骨架自己的测试（前端）：按 --skip-web 跳过")
        return bool(ok)

    print("\n5. 骨架自己的测试（前端）")
    if not npm:
        print("   SKIP 找不到 npm，跳过前端（用 --skip-web 可以显式跳过）")
        return bool(ok)
    for script in ("lint", "typecheck", "test", "build"):
        ok &= run(f"npm run {script}", [npm, "run", script], target / "web")
    return bool(ok)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--copy", help="副本目录（默认建在临时目录里）")
    parser.add_argument("--keep", action="store_true", help="保留副本，结束后不删除")
    parser.add_argument("--skip-web", action="store_true", help="只验证后端")
    parser.add_argument("--skip-backend", action="store_true", help="只验证前端（CI 的 web job 用这个）")
    args = parser.parse_args()

    if args.copy:
        target = Path(args.copy).resolve()
        if target.exists():
            shutil.rmtree(target)
        keep = True
    else:
        target = Path(tempfile.mkdtemp(prefix="migration-rehearsal-"))
        keep = args.keep

    try:
        prepare_copy(target)
        strip_business(target)
        passed = verify(target, args.skip_web, args.skip_backend)
    finally:
        if keep:
            print(f"\n副本保留在 {target}")
        else:
            shutil.rmtree(target, ignore_errors=True)

    if not passed:
        print("\n::error::迁移预演失败：删掉示例业务后骨架没有自洽——这正是迁移时会踩到的坑")
        return 1
    print("\n迁移预演通过：零业务状态下骨架自洽（这就是迁移的起点）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
