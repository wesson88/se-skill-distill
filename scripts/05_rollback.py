"""
Rollback — 校验整个操作的写后哈希，再恢复 Markdown、证据和角色基因。
独立操作 ID 避免同秒记录混淆；旧 ledger 仅支持有所有权哈希的记录。
"""
from __future__ import annotations

import base64
import re
import sys
from pathlib import Path

import _common as common
from semantic_publication import (decode_prior, restore_snapshots, sha256_bytes,
                                  sha256_file, snapshot, vault_path)


_ACTIONS = {"publication_committed", "frontmatter_patched", "skill_file_copied"}


def _run_id(record: dict) -> str:
    return record.get("run_id") or record.get("ts", "")[:19]


def list_runs() -> list[dict]:
    """Use explicit transaction IDs; retain timestamp lookup for old ledgers."""
    records = common.read_ledger()
    rolled_back = {record.get("rollback_of_run") for record in records
                   if record.get("action") == "publication_rolled_back"}
    runs = {}
    for record in records:
        if record.get("action") not in _ACTIONS:
            continue
        run_id = _run_id(record)
        if run_id not in rolled_back:
            runs.setdefault(run_id, []).append(record)
    return [{"run_id": key, "actions": len(values), "last_action": values[-1]["action"]}
            for key, values in runs.items()]


def _legacy_states(record: dict) -> list[dict]:
    """Unbound historical entries cannot safely delete or overwrite user data."""
    path = vault_path(record.get("relpath", ""), relative=True)
    action = record["action"]
    if action == "skill_file_copied":
        expected = record.get("artifact_sha256") or record.get("after_sha256")
        if not expected or sha256_file(path) != expected:
            raise ValueError("Legacy skill has no matching ownership digest")
        states = [{"relpath": record["relpath"], "before_b64": None,
                   "before_sha256": None, "after_sha256": expected}]
        sidecar = vault_path(path.with_suffix(".evidence.json"))
        if sidecar.exists():
            digest = record.get("evidence_sha256")
            if not digest or sha256_file(sidecar) != digest:
                raise ValueError("Legacy sidecar has no matching ownership digest")
            states.append({"relpath": sidecar.relative_to(common.VAULT_ROOT).as_posix(),
                           "before_b64": None, "before_sha256": None, "after_sha256": digest})
        return states
    expected = record.get("after_sha256")
    if not expected or sha256_file(path) != expected:
        raise ValueError("Legacy gene has no matching post-update digest")
    prior_digest = record.get("before_sha256")
    if not isinstance(prior_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", prior_digest):
        raise ValueError("Legacy backup has no valid prior-state digest")
    backup = vault_path(path.with_suffix(path.suffix + ".bak"))
    data = backup.read_bytes()
    if sha256_bytes(data) != prior_digest:
        raise ValueError("Legacy backup digest mismatch")
    return [{"relpath": record["relpath"], "before_b64": base64.b64encode(data).decode("ascii"),
             "before_sha256": sha256_bytes(data), "after_sha256": expected}]


def rollback_run(run_id: str, dry_run: bool = False) -> dict:
    """Validate every transaction member before restoring a generated pair/gene."""
    records = common.read_ledger()
    if any(record.get("action") == "publication_rolled_back" and record.get("rollback_of_run") == run_id
           for record in records):
        return {"ok": False, "error": "Run has already been rolled back"}
    targets = [record for record in records if record.get("action") in _ACTIONS and _run_id(record) == run_id]
    if not targets:
        return {"ok": False, "error": "Run not found", "run_id": run_id}
    states = []
    try:
        for record in reversed(targets):
            members = record.get("files") if record["action"] == "publication_committed" else _legacy_states(record)
            if not isinstance(members, list) or not members:
                raise ValueError("Transaction has no recoverable file states")
            states.extend(members)
        seen = set()
        for state in states:
            path = vault_path(state["relpath"], relative=True)
            if str(path.resolve()) in seen:
                raise ValueError("Run contains overlapping transactions; roll back operation IDs separately")
            seen.add(str(path.resolve()))
            decode_prior(state)
            if not state.get("after_sha256") or sha256_file(path) != state["after_sha256"]:
                raise ValueError("Generated file was edited or removed; rollback refused")
        current = [snapshot(vault_path(state["relpath"], relative=True)) for state in states]
    except Exception as exc:
        return {"ok": False, "run_id": run_id, "error": str(exc), "restored": [], "removed": [],
                "failed": [{"error": str(exc)}]}
    restored = [{"relpath": state["relpath"]} for state in states if state["before_b64"] is not None]
    removed = [{"relpath": state["relpath"]} for state in states if state["before_b64"] is None]
    if not dry_run:
        try:
            restore_snapshots(states)
            common.append_ledger({"action": "publication_rolled_back", "status": "ok", "rollback_of_run": run_id})
        except Exception as exc:
            try:
                restore_snapshots(current)
            except Exception as compensation:
                return {"ok": False, "run_id": run_id, "error": str(exc), "rollback_error": str(compensation)}
            return {"ok": False, "run_id": run_id, "error": str(exc)}
    return {"ok": True, "run_id": run_id, "restored": restored, "removed": removed, "failed": []}


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
