"""
阶段 4: Route — 角色路由 + 数字递增 + 中文标题

输入: AuditResult(approved) + draft
输出: RoutedSkill(目标文件名 + 目标绝对路径)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import _common
from _common import (
    AuditResult,
    ROLE_TO_PREFIX,
    ROLE_TO_SUBDIR,
    ROLE_TO_GENE_FILE,
    RoutedSkill,
    UNKNOWN_FILE,
    detect_role_from_text,
    find_next_skill_number,
    safe_filename,
)


def _skill_dir() -> Path:
    """SKILL_DIR 动态查(vault 路径可能 set_vault_root() 后被改)。"""
    return _common.SKILL_DIR


# ---------------------------------------------------------------------------
# 角色路由(单一权威)
# ---------------------------------------------------------------------------

def resolve_role(target_role: str | None, draft: dict, source: dict) -> str | None:
    """
    解析最终路由角色。
    优先级:
      1. target_role 显式指定且 != 'auto'
      2. draft 内容(concat)关键词命中
      3. source url/notes 关键词命中
      4. None(标记 unknown,不入库)
    """
    if target_role and target_role != "auto" and target_role in ROLE_TO_PREFIX:
        return target_role

    # 收集候选文本
    candidates: list[str] = []
    meta = draft.get("meta", {})
    candidates.append(meta.get("name", ""))
    candidates.append(meta.get("intent", ""))
    le = draft.get("learning_enhancement", {})
    candidates.append(le.get("plain_summary", ""))
    for kc in draft.get("key_concepts", []):
        candidates.append(kc.get("title", ""))
        candidates.append(kc.get("explanation", ""))
    for tag in le.get("knowledge_tags", []):
        candidates.append(tag)
    for kw in meta.get("trigger_keywords", []):
        candidates.append(kw)

    text_draft = "\n".join(candidates)
    role = detect_role_from_text(text_draft)
    if role:
        return role

    # fallback: 从 source url/notes
    text_src = (source.get("url") or "") + " " + (source.get("notes") or "")
    role = detect_role_from_text(text_src)
    return role  # None or str


# ---------------------------------------------------------------------------
# 中文标题生成
# ---------------------------------------------------------------------------

def extract_chinese_title(draft: dict) -> str:
    """
    从 draft 提取最佳中文标题(≤ 12 字)。
    优先级: meta.name > key_concepts[0].title > "未命名"
    """
    meta = draft.get("meta", {})
    name = meta.get("name", "").strip()

    if not name:
        kc = draft.get("key_concepts", [])
        if kc:
            name = kc[0].get("title", "").strip()

    if not name:
        name = "未命名"

    # 截断到 12 字
    if len(name) > 12:
        name = name[:12]
    return name


# ---------------------------------------------------------------------------
# 路由单条
# ---------------------------------------------------------------------------

def route_one(
    audit_result: AuditResult,
    draft: dict,
    source: dict,
    target_role_override: str | None = None,
) -> RoutedSkill | None:
    """
    路由单条 approved draft → RoutedSkill。
    返回 None 表示无法路由(入 unknown-queue)。
    """
    role = resolve_role(target_role_override, draft, source)
    if not role or role not in ROLE_TO_PREFIX:
        return None

    prefix = ROLE_TO_PREFIX[role]
    subdir_name = ROLE_TO_SUBDIR[role]
    subdir = _skill_dir() / subdir_name

    # 找下一个 N
    next_n = find_next_skill_number(subdir, prefix)
    # 中文标题
    title = extract_chinese_title(draft)
    safe_title = safe_filename(title, max_len=12)
    filename = f"{prefix}{next_n}-{safe_title}.md"

    new_path = subdir / filename

    return RoutedSkill(
        draft_path=audit_result.draft_path,
        role=role,
        prefix=prefix,
        next_n=next_n,
        new_filename=filename,
        new_path=new_path,
        target_subdir=subdir,
    )


# ---------------------------------------------------------------------------
# unknown queue
# ---------------------------------------------------------------------------

def append_unknown_queue(audit_result: AuditResult, source: dict, reason: str) -> None:
    """入 unknown 队列,等待人工。"""
    UNKNOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
    with UNKNOWN_FILE.open("a", encoding="utf-8") as f:
        f.write(f"\n## {source.get('url') or source.get('path', '?')}\n")
        f.write(f"- reason: {reason}\n")
        f.write(f"- conf: {audit_result.coverage:.1f}%\n")
        f.write(f"- title: {source.get('title', '?')}\n")
        f.write(f"- source_hash: {audit_result.source_hash}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse
    from rich.console import Console

    parser = argparse.ArgumentParser(description="阶段 4: 路由 approved drafts")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    console = Console()
    approved_path = Path("_state/audit/approved.jsonl")
    if not approved_path.exists():
        console.print(f"[red]找不到 {approved_path},先跑阶段 3[/red]")
        return 1

    routed: list[RoutedSkill] = []
    unknowns = 0
    with approved_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ar = AuditResult(**json.loads(line))
            # 读草稿
            draft = _load_draft(ar.draft_path)
            if not draft:
                console.print(f"[yellow]草稿丢失: {ar.draft_path}[/yellow]")
                continue
            source = ar.__dict__.get("source") or {"url": "", "notes": ""}
            target_role = ar.role
            rs = route_one(ar, draft, source, target_role_override=target_role)
            if rs is None:
                append_unknown_queue(ar, source, "role resolve failed")
                unknowns += 1
                continue
            routed.append(rs)
            console.print(
                f"  {ar.coverage:.1f}%  {rs.role}  {rs.new_filename}  ←  {ar.draft_path}"
            )

    console.print(
        f"\n[bold]路由完成[/bold] 成功 {len(routed)} 条,unknown {unknowns} 条"
    )
    # 写路由结果供下一阶段用
    routed_path = Path("_state/routed.jsonl")
    with routed_path.open("w", encoding="utf-8") as f:
        for rs in routed:
            f.write(json.dumps({
                "draft_path": rs.draft_path,
                "role": rs.role,
                "prefix": rs.prefix,
                "next_n": rs.next_n,
                "new_filename": rs.new_filename,
                "new_path": str(rs.new_path),
                "target_subdir": str(rs.target_subdir),
            }, ensure_ascii=False) + "\n")
    return 0


def _load_draft(draft_path: str) -> dict | None:
    if not draft_path or not Path(draft_path).exists():
        return None
    return json.loads(Path(draft_path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    sys.exit(main())
