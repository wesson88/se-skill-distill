"""
阶段 5: Rollback — 读 ledger,逐条 reverse

回滚两种 action:
  - frontmatter_patched → 用 .bak 恢复 + 删 .bak
  - skill_file_copied   → 删除 vault 文件

回滚顺序: 先恢复 frontmatter(破坏性大),再删 vault 文件(cheap)
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from _common import LEDGER_FILE, STATE_DIR, append_ledger, read_ledger, VAULT_ROOT


def list_runs() -> list[dict]:
    """列出所有 run_id(从 ledger 时间戳聚合)。"""
    records = read_ledger()
    # 按 ts 分组
    runs: dict[str, list[dict]] = {}
    for r in records:
        ts = r.get("ts", "")
        # 截取到秒作为 run_id
        run_id = ts[:19] if len(ts) >= 19 else ts
        runs.setdefault(run_id, []).append(r)
    return [{"run_id": k, "actions": len(v), "last_action": v[-1].get("action", "?")} for k, v in sorted(runs.items())]


def rollback_run(run_id: str, dry_run: bool = False) -> dict:
    """
    回滚指定 run_id 的所有 action。
    顺序: frontmatter_patched(先) → skill_file_copied(后)
    """
    records = read_ledger()
    target_records = [r for r in records if r.get("ts", "")[:19] == run_id]

    if not target_records:
        return {"ok": False, "error": f"找不到 run_id: {run_id}"}

    # 排序: frontmatter_patched 先,skill_file_copied 后
    priority = {"frontmatter_patched": 0, "skill_file_copied": 1}
    target_records.sort(key=lambda r: priority.get(r.get("action", ""), 99))

    restored: list[dict] = []
    removed: list[dict] = []
    failed: list[dict] = []

    for r in target_records:
        action = r.get("action", "")
        relpath = r.get("relpath", "")
        if not relpath:
            continue
        full = VAULT_ROOT / relpath

        if action == "frontmatter_patched":
            backup = full.with_suffix(full.suffix + ".bak")
            if not backup.exists():
                failed.append({"relpath": relpath, "action": action, "error": ".bak 不存在"})
                continue
            if dry_run:
                restored.append({"relpath": relpath, "action": action, "from": str(backup)})
                continue
            try:
                shutil.copy2(backup, full)
                backup.unlink()
                restored.append({"relpath": relpath, "action": action})
                append_ledger({
                    "action": "frontmatter_restored",
                    "relpath": relpath,
                    "status": "ok",
                    "rollback_of_run": run_id,
                })
            except Exception as e:
                failed.append({"relpath": relpath, "action": action, "error": str(e)})

        elif action == "skill_file_copied":
            if not full.exists():
                failed.append({"relpath": relpath, "action": action, "error": "文件不存在"})
                continue
            if dry_run:
                removed.append({"relpath": relpath, "action": action})
                continue
            try:
                full.unlink()
                removed.append({"relpath": relpath, "action": action})
                append_ledger({
                    "action": "skill_file_removed",
                    "relpath": relpath,
                    "status": "ok",
                    "rollback_of_run": run_id,
                })
            except Exception as e:
                failed.append({"relpath": relpath, "action": action, "error": str(e)})

    return {
        "ok": len(failed) == 0,
        "run_id": run_id,
        "restored": restored,
        "removed": removed,
        "failed": failed,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    from rich.console import Console

    parser = argparse.ArgumentParser(description="回滚指定 run_id")
    parser.add_argument("--run-id", help="要回滚的 run_id(从 --list 看)")
    parser.add_argument("--list", action="store_true", help="列出所有 run_id")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    console = Console()

    if args.list:
        runs = list_runs()
        if not runs:
            console.print("[yellow]ledger 为空,无运行记录[/yellow]")
            return 0
        console.print("[bold]运行历史(从 ledger 聚合)[/bold]")
        for r in runs:
            console.print(f"  {r['run_id']}  actions={r['actions']}  last={r['last_action']}")
        return 0

    if not args.run_id:
        # 默认:回滚最近一次
        runs = list_runs()
        if not runs:
            console.print("[yellow]ledger 为空,无运行记录[/yellow]")
            return 1
        run_id = runs[-1]["run_id"]
        console.print(f"[cyan]未指定 --run-id,使用最近一次: {run_id}[/cyan]")
    else:
        run_id = args.run_id

    result = rollback_run(run_id, dry_run=args.dry_run)

    if not result["ok"]:
        console.print(f"[red]回滚失败[/red] {result.get('error')}")
        return 1

    console.print(f"[bold]回滚 {run_id}[/bold] (dry_run={args.dry_run})")
    if result["restored"]:
        console.print(f"  [green]restored {len(result['restored'])} frontmatter:[/green]")
        for r in result["restored"]:
            console.print(f"    {r['relpath']}")
    if result["removed"]:
        console.print(f"  [green]removed {len(result['removed'])} skill files:[/green]")
        for r in result["removed"]:
            console.print(f"    {r['relpath']}")
    if result["failed"]:
        console.print(f"  [red]failed {len(result['failed'])}:[/red]")
        for r in result["failed"]:
            console.print(f"    {r['relpath']}: {r['error']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
