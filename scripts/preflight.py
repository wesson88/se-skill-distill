#!/usr/bin/env python
"""前置检查:skill 启动时验证 vault + skillmind,不就绪则友好报告并 exit(1)。

独立模块(不 import `_common`),避免触发 `_common` 模块加载时的 vault 解析硬抛错。
被 pipeline.py / distill_url.py 在 `from _common import ...` 之前调用。

设计:
  - vault:优先 SE_VAULT;未设则尝试从 skill 位置推导(本地 vault 内开发)。
           全局安装(推导失效)且未设 SE_VAULT → fail,强制用户配置。
  - skillmind:必须可 import(否则任何命令都跑不了)→ fail。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent

# 与 _common.SKILLMIND_ROOT 同来源(不 import _common,自己算)
SKILLMIND_ROOT = Path(os.environ.get("SKILLMIND_REPO") or r"E:\workstation\ai\skillMind")


def _check_vault() -> tuple[str, str]:
    """返回 (status, msg)。status ∈ ok | fail。"""
    sev = os.environ.get("SE_VAULT")
    if sev:
        v = Path(sev)
        if not v.exists():
            return ("fail", f"SE_VAULT={sev} 路径不存在")
        missing = [d for d in ("00-系统", "20-知识") if not (v / d).exists()]
        if missing:
            return ("fail", f"SE_VAULT={sev} 缺子目录 {missing}(需 00-系统/ + 20-知识/)")
        return ("ok", f"SE_VAULT={sev}")
    # SE_VAULT 未设:尝试从 skill 位置推(本地 vault 内开发)
    derived = SKILL_ROOT.parent.parent.parent
    if (derived / "00-系统").exists() and (derived / "20-知识").exists():
        return ("ok", f"未设 SE_VAULT,从 skill 位置推导 vault={derived}(本地开发模式)")
    return ("fail",
            "SE_VAULT 未设,且 skill 不在 <vault>/.claude/skills/ 下(全局安装?),无法推导 vault\n"
            "      → export SE_VAULT=<你的 vault 根>(推荐),或运行时加 --vault <path>")


def _check_skillmind() -> tuple[str, str]:
    """返回 (status, msg)。"""
    if str(SKILLMIND_ROOT) not in sys.path:
        sys.path.insert(0, str(SKILLMIND_ROOT))
    try:
        import skillmind  # noqa: F401
        return ("ok", f"skillmind 可 import(从 {SKILLMIND_ROOT})")
    except ImportError as e:
        return ("fail",
                f"skillmind 无法 import({e})\n"
                f"      → pip install skillmind,或 export SKILLMIND_REPO=<本地 skillMind 仓库>")


def check() -> None:
    """前置检查。任一 fail 则打印清单 + sys.exit(1)。"""
    print("preflight: 前置检查 vault + skillmind ...")
    items = [("vault", _check_vault()), ("skillmind", _check_skillmind())]
    has_fail = False
    for name, (status, msg) in items:
        mark = "✓" if status == "ok" else "✗"
        print(f"  {mark} {name}: {msg}")
        if status == "fail":
            has_fail = True
    if has_fail:
        print("\npreflight 失败 —— 请先解决 ✗ 项再使用 se-skill-distill。")
        sys.exit(1)
    print("preflight 通过。\n")


if __name__ == "__main__":
    check()
