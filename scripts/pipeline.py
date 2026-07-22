"""
Pipeline 编排器: 串联 6 阶段(ingest → extract → publish → audit → route → inject)

v2: 阶段 3 audit 改用 skillMind LLM audit_source,本层按 kind 加权算 score。

用法:
    python pipeline.py --config sources.yaml [--dry-run] [--skip-extract] [--skip-publish] [--status]
    python pipeline.py --status
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from rich.console import Console

# 前置检查(在 import _common 前,避开其模块加载时的 vault 硬解析)
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
import preflight as _preflight
_preflight.check()

from _common import (
    LEDGER_FILE,
    STATE_DIR,
    ensure_skillmind_venv,
    import_skillmind,
)


def load_sources(config_path: Path) -> list[dict]:
    """加载 sources.yaml。"""
    import yaml
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    sources = cfg.get("sources", [])
    if not sources:
        raise ValueError(f"sources.yaml 中无 sources 列表: {config_path}")
    # 校验每条
    for i, s in enumerate(sources):
        if "kind" not in s:
            raise ValueError(f"第 {i+1} 条缺 kind: {s}")
        if s["kind"] in ("github_repo", "github_raw", "rss", "single_url") and not s.get("url"):
            raise ValueError(f"第 {i+1} 条 kind={s['kind']} 缺 url")
        if s["kind"] == "local_dir" and not s.get("path"):
            raise ValueError(f"第 {i+1} 条 kind=local_dir 缺 path")
    return sources


def run_pipeline(config_path: Path, dry_run: bool = False, skip_extract: bool = False, domain: str = "se") -> int:
    """跑完整 5 阶段流水线。"""
    from rich.console import Console
    console = Console()

    ensure_skillmind_venv()
    sources = load_sources(config_path)
    console.print(f"[bold]=== skillmind-to-vault 蒸馏流水线 ===[/bold]")
    console.print(f"config: {config_path}")
    console.print(f"dry_run: {dry_run}  skip_extract: {skip_extract}")
    console.print(f"源数: {len(sources)}")
    console.print()

    # 阶段 1: ingest
    console.print("[bold]━━━ 阶段 1: ingest ━━━[/bold]")
    from importlib.machinery import SourceFileLoader
    ingest_mod = SourceFileLoader("ingest", str(Path(__file__).parent / "01_ingest.py")).load_module()
    batch_results = ingest_mod.ingest_batch(sources, console=console)

    # 收集 source_infos(包括 skipped 的 — 已缓存但内容可继续 extract)
    source_infos: list[dict] = []
    for _, docs in batch_results:
        for d in docs:
            source_infos.append(d)

    n_skipped = sum(1 for d in source_infos if d.get("skipped"))
    n_new = len(source_infos) - n_skipped
    console.print(f"\n  实际可处理: {len(source_infos)} (新增 {n_new} / 已缓存 {n_skipped})")
    if not source_infos:
        console.print("[yellow]无新增源,流水线结束[/yellow]")
        return 0

    # 阶段 2: extract
    if not skip_extract:
        console.print("\n[bold]━━━ 阶段 2: extract (LLM) ━━━[/bold]")
        audit_mod = SourceFileLoader("audit", str(Path(__file__).parent / "02_audit.py")).load_module()
        extract_results = audit_mod.extract_batch(source_infos, console=console)
    else:
        console.print("\n[bold yellow]━━━ 阶段 2: extract 跳过 ━━━[/bold yellow]")
        # skip-extract 时:用阶段 1 收集的 source_infos(含已缓存源)供 audit 循环用
        extract_results = [{"source": d, "drafts": []} for d in source_infos]

    # 阶段 2.5: publish_to_vault(必须先于 audit)
    console.print("\n[bold]━━━ 阶段 2.5: publish_to_vault ━━━[/bold]")
    audit_mod = SourceFileLoader("audit", str(Path(__file__).parent / "02_audit.py")).load_module()
    if not dry_run:
        n_published = audit_mod.publish_all_drafts(console=console)
        if n_published == 0 and not skip_extract:
            console.print("[yellow]警告: 无草稿 publish(可能 drafts/ 为空)[/yellow]")
    else:
        console.print("  [yellow]dry_run: 跳过 publish[/yellow]")

    # 阶段 3: audit(直接读 skillMind 精简 AuditReport,coverage ≥ 90% → PASS)
    console.print("\n[bold]━━━ 阶段 3: audit (skillMind LLM, 90% 阈值) ━━━[/bold]")

    audit_results: list = []
    seen_hashes: set[str] = set()
    for er in extract_results:
        src = er["source"]
        sh = src.get("source_hash", "")
        if not sh or sh in seen_hashes:
            continue
        seen_hashes.add(sh)
        # 角色推断:target_role 优先,否则用关键词
        target_role = src.get("target_role", "auto")
        if target_role and target_role != "auto":
            role = target_role
        else:
            role = _resolve_role_for_audit(target_role, er.get("drafts", [{}])[0] if er.get("drafts") else {}, src)
        try:
            ar = audit_mod.audit_one(src, role)
            audit_results.append(ar)
            console.print(
                f"  coverage={ar.coverage:.1f}%  verdict={ar.verdict}  "
                f"needs_review={ar.needs_human_review}  halluc={ar.hallucination_rate:.2f}  "
                f"missing={len(ar.missing)}  role={ar.role}"
            )
            for note in ar.notes:
                console.print(f"    [yellow]note:[/yellow] {note}")
        except Exception as e:
            console.print(f"  [bold red]FAIL[/bold red] audit ({sh[:12]}): {e}")

    audit_mod.write_audit_results(audit_results)

    n_pass = sum(1 for r in audit_results if r.verdict == "PASS")
    n_fail = sum(1 for r in audit_results if r.verdict == "FAIL")
    console.print(f"\n  PASS={n_pass}  FAIL={n_fail}  / 总源 {len(audit_results)}")

    if n_pass == 0:
        console.print("[yellow]无 PASS(coverage 都 < 90%),后续阶段跳过[/yellow]")
        return 0

    # 阶段 4: route
    console.print("\n[bold]━━━ 阶段 4: route ━━━[/bold]")
    route_mod = SourceFileLoader("route", str(Path(__file__).parent / "03_route.py")).load_module()

    routed: list = []
    unknowns = 0
    for ar in audit_results:
        if ar.verdict != "PASS":
            continue
        draft = _load_draft(ar.draft_path)
        if not draft:
            console.print(f"  [red]草稿丢失: {ar.draft_path}[/red]")
            continue
        src = {"url": "", "notes": ""}  # source_info 不全,简化
        rs = route_mod.route_one(ar, draft, src, target_role_override=ar.role, domain=domain)
        if rs is None:
            route_mod.append_unknown_queue(ar, src, "role resolve failed")
            unknowns += 1
            continue
        routed.append(rs)
        console.print(f"  {ar.coverage:.1f}%  {rs.role}  {rs.new_filename}")

    console.print(f"\n  routed={len(routed)} unknown={unknowns}")
    if not routed:
        console.print("[yellow]无路由成功,跳过 inject[/yellow]")
        return 0

    # 写 routed.jsonl 给阶段 5
    routed_path = STATE_DIR / "routed.jsonl"
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
                "domain": rs.domain,
            }, ensure_ascii=False) + "\n")

    # 阶段 5: inject
    console.print("\n[bold]━━━ 阶段 5: inject ━━━[/bold]")
    inject_mod = SourceFileLoader("inject", str(Path(__file__).parent / "04_inject.py")).load_module()

    injected: list = []
    failed: list = []
    for rs in routed:
        draft = _load_draft(rs.draft_path)
        if not draft:
            continue
        result = inject_mod.inject_one(rs, draft, dry_run=dry_run)
        if result["ok"]:
            injected.append((rs, draft))
            console.print(f"  [green]✓[/green] {rs.new_filename} → {rs.role}")
        else:
            console.print(f"  [red]✗ {rs.new_filename} 失败 @ {result['stage']}: {result.get('error')}[/red]")
            failed.append({"routed": rs, "error": result.get("error"), "stage": result.get("stage")})

    # 写复盘日志(非 dry_run)
    if not dry_run and injected:
        patches = [r["patch"] for _, r in [(None, i) for i in injected]] if False else []
        # 实际 patches 需重 inject 拿
        patches = []
        for rs, _ in injected:
            p = inject_mod.patch_role_gene(rs.role, rs.new_filename, dry_run=True)
            if p["ok"]:
                patches.append(p)
        inject_mod.write_retrospective_log(injected, patches)

    console.print(
        f"\n[bold]=== 完成 ===[/bold]  injected={len(injected)}  failed={len(failed)}"
    )
    if failed:
        console.print("[yellow]部分失败,自动回滚已执行(见 ledger)[/yellow]")
        return 1
    return 0


def show_status() -> int:
    """显示最近运行状态 + ledger 概览。"""
    from rich.console import Console
    console = Console()
    console.print("[bold]=== 运行状态 ===[/bold]")
    console.print(f"ledger: {LEDGER_FILE}")
    if not LEDGER_FILE.exists():
        console.print("  [dim]ledger 不存在,无运行记录[/dim]")
        return 0
    records = [json.loads(line) for line in LEDGER_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    n_skill = sum(1 for r in records if r.get("action") == "skill_file_copied")
    n_patch = sum(1 for r in records if r.get("action") == "frontmatter_patched")
    n_restore = sum(1 for r in records if r.get("action") == "frontmatter_restored")
    console.print(f"  写入 vault skill: {n_skill}")
    console.print(f"  改 frontmatter:   {n_patch}")
    console.print(f"  回滚:             {n_restore}")
    return 0


def _resolve_role_for_audit(target_role, draft, source) -> str:
    """resolve role for audit(简化:用 target_role 或 关键词)."""
    from _common import detect_role_from_text
    if target_role and target_role != "auto":
        return target_role
    text = (source.get("url") or "") + " " + (source.get("notes") or "")
    return detect_role_from_text(text) or "后端工程师"


def _load_draft(draft_path: str) -> dict | None:
    if not draft_path or not Path(draft_path).exists():
        return None
    return json.loads(Path(draft_path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    import _common
    parser = argparse.ArgumentParser(description="skillmind-to-vault 蒸馏流水线")
    parser.add_argument("--config", help="sources.yaml 路径")
    parser.add_argument("--vault", help="vault 根路径(默认从脚本位置推导;也可设 SE_VAULT 环境变量)")
    parser.add_argument("--dry-run", action="store_true", help="不写盘,只输出 diff")
    parser.add_argument("--skip-extract", action="store_true", help="跳过 extract(已存在 drafts/)")
    parser.add_argument("--status", action="store_true", help="显示运行状态")
    parser.add_argument("--domain", default="se", help="角色域(默认 se;agent-workflow 当前只扫 se)")
    args = parser.parse_args(argv)

    if args.status:
        return show_status()

    if not args.config:
        parser.error("需要 --config 或 --status")

    # 必须在所有 vault 派生路径使用前设
    _common.set_vault_root(args.vault)
    console = Console()
    console.print(f"  [dim]vault 根:{_common.VAULT_ROOT}[/dim]")

    config_path = Path(args.config).resolve()
    if not config_path.exists():
        print(f"[ERROR] 找不到 config: {config_path}")
        return 1

    return run_pipeline(config_path, dry_run=args.dry_run, skip_extract=args.skip_extract, domain=args.domain)


if __name__ == "__main__":
    sys.exit(main())
