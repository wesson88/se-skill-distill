"""
阶段 2+3: Extract + Audit (v3 — 对齐 skillMind 2.3+ 精简 audit)

- 阶段 2: 调 skillmind.extractor.extract_skill (LLM 调用)
- 阶段 2.5: 调 skillmind publish --all,把草稿发到 ~/.skillmind/vault/skills/
- 阶段 3: 调 skillmind.auditor.audit_source (LLM Pass 1+2),本层不重算 score
         coverage ≥ 90% → PASS(自动放行,无需 review)
         coverage < 90% → FAIL(needs_human_review=True,走人工)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from _common import (
    AuditResult,
    AUDIT_DIR,
    APPROVED_FILE,
    REJECT_FILE,
    REVIEW_FILE,
    ensure_skillmind_venv,
    import_skillmind,
)


# ---------------------------------------------------------------------------
# 阶段 2: Extract(保留原 v1 实现)
# ---------------------------------------------------------------------------

def extract_one(source_info: dict, console=None) -> list[dict]:
    """调 skillMind extractor 对单个 source 提取知识单元。"""
    ensure_skillmind_venv()
    _, extractor, config = import_skillmind()
    cfg = config.load_config()

    raw_path = source_info["raw_path"]
    drafts = extractor.extract_skill(raw_path, source_info, cfg, console=console)
    return drafts


def extract_batch(source_infos: list[dict], console=None) -> list[dict]:
    """批量 extract。"""
    results: list[dict] = []
    for i, info in enumerate(source_infos, 1):
        if console:
            console.print(f"[cyan]({i}/{len(source_infos)}) 提取:[/cyan] {info.get('title') or info.get('source_url', '')}")
        try:
            drafts = extract_one(info, console=console)
            results.append({"source": info, "drafts": drafts})
            if console:
                console.print(f"  [green]OK[/green] {len(drafts)} 张草稿")
        except Exception as e:
            if console:
                console.print(f"  [bold red]FAIL[/bold red] 失败: {e}")
            results.append({"source": info, "drafts": [], "error": str(e)})
    return results


# ---------------------------------------------------------------------------
# 阶段 2.5: publish_to_vault(新)
# ---------------------------------------------------------------------------

def publish_all_drafts(console=None) -> int:
    """调 skillmind publish --all,把 drafts/ 下草稿发到 vault/skills/。

    audit_source 要求"笔记已 publish 到 vault"才能扫到 frontmatter.source_hash。
    """
    proc = subprocess.run(
        ["skillmind", "publish", "--all"],
        capture_output=True, text=True, encoding="utf-8",
    )
    if proc.returncode != 0:
        if console:
            console.print(f"  [bold red]FAIL[/bold red] publish 失败: {proc.stderr[:200]}")
        return 0
    # 解析 stdout 拿 published 数量
    n_published = 0
    for line in proc.stdout.splitlines():
        if "✓" in line or "published" in line.lower():
            n_published += 1
    if console:
        console.print(f"  [green]OK[/green] published {n_published} 草稿")
    return n_published


# ---------------------------------------------------------------------------
# 阶段 3: Audit(直接读 skillMind 精简 AuditReport,不再本层重算)
# ---------------------------------------------------------------------------

def audit_one(source_info: dict, role: str) -> AuditResult:
    """
    对单个 source_hash 调 skillMind audit_source,直接读 verdict + coverage + missing。
    skillMind 的 LLM Pass 1+2 已是 ground truth,本层不再算 score / kind_coverages。

    重要: 调之前必须已 publish 草稿到 vault/skills/(阶段 2.5)。
    同一 source_hash 多张草稿 → 1 次 audit(Pass 1 抽 items 不重复)。
    """
    ensure_skillmind_venv()
    _, _, config_mod = import_skillmind()
    import skillmind.auditor as auditor
    cfg = config_mod.load_config()

    raw_path = Path(source_info["raw_path"])
    if not raw_path.exists():
        raise FileNotFoundError(f"原文不存在: {raw_path}")

    vault_skills = Path.home() / ".skillmind" / "vault" / "skills"
    if not vault_skills.exists():
        raise RuntimeError(
            f"vault_skills 目录不存在: {vault_skills}\n"
            f"请先在 pipeline 阶段 2.5 publish 草稿"
        )

    # 调 skillMind audit_source(LLM Pass 1+2,返回精简 AuditReport)
    report = auditor.audit_source(
        source_info["source_hash"],
        cfg=cfg,
        console=None,
        max_extract_files=10,
        vault_skills_override=str(vault_skills),
    )

    # 简化 notes:只标注关键异常
    notes: list[str] = []
    if report.needs_human_review:
        notes.append(
            f"coverage {report.coverage_weighted:.1f}% < {auditor.PASS_COVERAGE_THRESHOLD:.0f}% 阈值,需人工 review"
        )
    if len(report.hallucinations) > 0:
        notes.append(
            f"hallucinations {len(report.hallucinations)} 条,数字/wikilink 失真"
        )

    return AuditResult(
        source_hash=source_info.get("source_hash", ""),
        draft_path=_find_draft_path(source_info.get("source_hash", "")),
        role=role,
        coverage=report.coverage_weighted,             # 0-100
        hallucination_rate=len(report.hallucinations) / max(len(report.items), 1),
        needs_human_review=report.needs_human_review,
        missing=report.missing,
        verdict=report.verdict,                        # PASS / FAIL
        notes=notes,
    )


def _find_draft_path(source_hash: str) -> str:
    """根据 source_hash 在 drafts/ 下找对应的 draft JSON 路径(route 阶段要读 JSON)。
    draft 文件中 source_hash 在嵌套 source.source_hash 里,兼容两种取法。
    """
    if not source_hash:
        return ""
    drafts_dir = Path.home() / ".skillmind" / "drafts"
    if not drafts_dir.exists():
        return ""
    for df in sorted(drafts_dir.glob("*.json")):
        try:
            d = json.loads(df.read_text(encoding="utf-8"))
        except Exception:
            continue
        # 优先:嵌套的 source.source_hash;fallback:顶层 source_hash
        sh = d.get("source", {}).get("source_hash") or d.get("source_hash")
        if sh == source_hash:
            return str(df)
    return ""


def write_audit_results(results: list[AuditResult]) -> None:
    """按 verdict 分文件写。对齐 skillMind 新判定:
       - PASS(≥90%) → approved.jsonl(自动放行)
       - FAIL(<90%) → manual-review.jsonl(需人工)
       - rejected.jsonl 保留以备硬错误(本版暂未用,空文件)
    """
    for fp in (APPROVED_FILE, REVIEW_FILE, REJECT_FILE):
        fp.parent.mkdir(parents=True, exist_ok=True)
        if fp.exists():
            fp.unlink()
    approved = [r for r in results if r.verdict == "PASS"]
    review = [r for r in results if r.verdict == "FAIL"]
    rejected: list[AuditResult] = []   # 新判定无 rejected,留空 list
    for fp, items in ((APPROVED_FILE, approved), (REVIEW_FILE, review), (REJECT_FILE, rejected)):
        with fp.open("w", encoding="utf-8") as f:
            for r in items:
                f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse
    from rich.console import Console

    parser = argparse.ArgumentParser(description="阶段 2+3: extract + publish + audit (v3)")
    parser.add_argument("--config", required=True, help="sources.yaml 路径")
    parser.add_argument("--role", help="target_role override")
    parser.add_argument("--skip-extract", action="store_true", help="跳过 extract,直接用 drafts/ 已存在草稿")
    parser.add_argument("--skip-publish", action="store_true", help="跳过阶段 2.5 publish(已发布过)")
    args = parser.parse_args(argv)

    console = Console()
    import yaml
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    sources = cfg.get("sources", [])

    # 1. ingest
    if not args.skip_extract:
        from skillmind.collector import list_cached
        _, _, config_mod = import_skillmind()
        cached = list_cached()
        source_infos: list[dict] = []
        for src in sources:
            target = src.get("url") or src.get("path", "")
            for c in cached:
                if c.get("source_url") == target or c.get("source_repo") == target:
                    source_infos.append(c)
                    break
        console.print(f"[bold]阶段 1+2: 找到 {len(source_infos)} 个已采集源,开始 extract[/bold]")
        extract_results = extract_batch(source_infos, console=console)
    else:
        extract_results = []
        # skip-extract 时:从 list_cached() 拿所有 source_info,用于 audit
        from skillmind.collector import list_cached
        cached = list_cached()
        # 转成与 extract_results 等价的 shape,便于后续 audit 循环
        extract_results = [{"source": c, "drafts": []} for c in cached]
        console.print(f"[bold]跳过 extract,直接 audit 已存在草稿 ({len(extract_results)} 源)[/bold]")

    # 2.5 publish(必须先于 audit)
    if not args.skip_publish:
        console.print(f"\n[bold]━━━ 阶段 2.5: publish_to_vault ━━━[/bold]")
        n = publish_all_drafts(console=console)
        if n == 0 and not args.skip_extract:
            console.print("[yellow]警告: 没有草稿被 publish,可能 drafts/ 为空[/yellow]")
    else:
        console.print("\n[bold yellow]━━━ 阶段 2.5: publish 跳过 ━━━[/bold yellow]")

    # 3. audit(每个 source_hash 一次,不复抽)
    console.print(f"\n[bold]━━━ 阶段 3: audit (skillMind LLM) ━━━[/bold]")
    seen_hashes: set[str] = set()
    audit_results: list[AuditResult] = []
    for er in extract_results:
        src = er["source"]
        sh = src.get("source_hash", "")
        if not sh or sh in seen_hashes:
            continue
        seen_hashes.add(sh)
        role = args.role or _infer_role_from_source(src)
        try:
            ar = audit_one(src, role)
            audit_results.append(ar)
            # 终端 summary:对齐 skillMind 精简 JSON
            console.print(
                f"  coverage={ar.coverage:.1f}%  verdict={ar.verdict}  "
                f"needs_review={ar.needs_human_review}  halluc={ar.hallucination_rate:.2f}  "
                f"missing={len(ar.missing)}"
            )
            for note in ar.notes:
                console.print(f"    [yellow]note:[/yellow] {note}")
        except Exception as e:
            console.print(f"  [bold red]FAIL[/bold red] audit 失败 ({sh[:12]}): {e}")

    write_audit_results(audit_results)
    n_pass = sum(1 for r in audit_results if r.verdict == "PASS")
    n_fail = sum(1 for r in audit_results if r.verdict == "FAIL")
    console.print(
        f"\n[bold green]完成[/bold green] PASS={n_pass} "
        f"FAIL={n_fail} / 总源 {len(audit_results)}"
    )
    return 0


def _infer_role_from_source(source: dict) -> str:
    """从 sources.yaml 推断角色:优先 target_role,否则用源 url/text 关键词。"""
    tr = source.get("target_role")
    if tr and tr != "auto":
        return tr
    text = (source.get("url") or "") + " " + (source.get("notes") or "")
    from _common import detect_role_from_text
    role = detect_role_from_text(text)
    return role or "后端工程师"


if __name__ == "__main__":
    sys.exit(main())
