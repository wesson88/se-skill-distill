"""Scoped SkillMind v2.5 extraction, audit, routing and evidence-aware injection."""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sys
import uuid
from rich.console import Console

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import preflight
import _common
from _common import AuditResult, atomic_write_text, ensure_skillmind_venv, format_coverage


def load_sources(config_path: Path) -> list[dict]:
    import yaml
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    sources = config.get("sources", []) if isinstance(config, dict) else []
    if not isinstance(sources, list) or not sources:
        raise ValueError("sources.yaml 需要非空 sources 列表")
    kinds = {"github_repo", "github_raw", "rss", "single_url", "local_dir"}
    for source in sources:
        if not isinstance(source, dict) or source.get("kind") not in kinds:
            raise ValueError("来源 kind 无效")
        field = "path" if source["kind"] == "local_dir" else "url"
        if not source.get(field):
            raise ValueError(f"来源缺少 {field}")
    return sources


def _load_stage(filename: str):
    name = "_se_" + Path(filename).stem
    spec = spec_from_file_location(name, SCRIPT_DIR / filename)
    module = module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_draft(path: str) -> dict | None:
    if not path:
        return None
    try:
        draft = json.loads(Path(path).read_text(encoding="utf-8"))
        return draft if isinstance(draft, dict) else None
    except (OSError, ValueError):
        return None


def run_pipeline(config_path: Path, dry_run: bool = False, skip_extract: bool = False, domain: str = "se") -> int:
    """Dry-run may call models and write staging, but never writes final vault files."""
    console = Console()
    ensure_skillmind_venv()
    sources = load_sources(config_path)
    console.print(f"[bold]SE 蒸馏[/bold] sources={len(sources)} dry_run={dry_run}")
    ingest = _load_stage("01_ingest.py")
    audit = _load_stage("02_audit.py")
    route = _load_stage("03_route.py")
    inject = _load_stage("04_inject.py")
    stage_root = _common.STATE_DIR / "runs" / uuid.uuid4().hex / "published"
    results: list[AuditResult] = []
    source_by_hash: dict[str, dict] = {}
    for requested, documents in ingest.ingest_batch(sources, console=console):
        if not documents:
            results.append(AuditResult("", "", requested.get("target_role", "auto"),
                                       notes=["采集失败或没有可处理来源"], audit_details={"request": requested}))
        for document in documents:
            source = dict(document)
            source["target_role"] = requested.get("target_role", "auto")
            source["notes"] = requested.get("notes", "")
            sh = source.get("source_hash")
            if not sh:
                results.append(AuditResult("", "", source["target_role"], notes=["采集结果缺少 source_hash"]))
            elif sh not in source_by_hash:
                source_by_hash[sh] = source
    for sh, source in source_by_hash.items():
        role = source["target_role"]
        try:
            if not skip_extract:
                extracted = audit.extract_batch([source], console=console)
                if (len(extracted) != 1 or extracted[0].get("error")
                        or not extracted[0].get("drafts")
                        or any(d.get("extract_complete", True) is not True for d in extracted[0]["drafts"])):
                    results.append(AuditResult(sh, "", role, notes=["提取失败或不完整，未发布/审计旧草稿"]))
                    continue
            stage = stage_root / hashlib.sha256(sh.encode("utf-8")).hexdigest()
            count = audit.publish_all_drafts([source], console=console, output_dir=stage)
            if not count:
                results.append(AuditResult(sh, "", role, notes=["没有完整草稿可供审计"]))
                continue
            report = audit.audit_one(source, role, vault_skills=stage)
            results.append(report)
            console.print(f"  coverage={format_coverage(report.coverage)} verdict={report.verdict} review={report.needs_human_review}")
        except Exception as error:
            results.append(AuditResult(sh, "", role, notes=[f"发布/审计失败：{type(error).__name__}"],
                                       audit_details={"error_type": type(error).__name__}))
    injected, patches, routed = [], [], []
    reserved: set[tuple[str, str, int]] = set()
    for report in results:
        if not report.is_approved:
            continue
        paths = report.draft_paths or ([report.draft_path] if report.draft_path else [])
        if not paths:
            report.verdict, report.needs_human_review = "UNKNOWN", True
            report.notes.append("审计未绑定草稿")
        for path in paths:
            try:
                draft = _load_draft(path)
                if not draft:
                    raise ValueError("missing draft")
                card_report = replace(report, draft_path=path)
                rs = route.route_one(card_report, draft, source_by_hash.get(report.source_hash, {}),
                                     target_role_override=report.role, domain=domain)
                if rs is None:
                    raise ValueError("unresolved role or unapproved audit")
                while (str(rs.target_subdir), rs.prefix, rs.next_n) in reserved:
                    rs.next_n += 1
                    rs.new_filename = f"{rs.prefix}{rs.next_n}-" + rs.new_filename.split("-", 1)[1]
                    rs.new_path = rs.target_subdir / rs.new_filename
                reserved.add((str(rs.target_subdir), rs.prefix, rs.next_n))
                routed.append(rs)
                outcome = inject.inject_one(rs, draft, dry_run=dry_run)
                if not outcome.get("ok"):
                    failure = {key: outcome[key] for key in ("stage", "error", "rollback_error") if key in outcome}
                    report.audit_details.setdefault("injection_errors", []).append(failure)
                    if isinstance(outcome.get("error"), str):
                        report.notes.append(outcome["error"])
                    raise ValueError(outcome.get("stage", "inject"))
                injected.append((rs, draft))
                patches.append(outcome["patch"])
                console.print(f"  {'预览' if dry_run else '已入库'} {rs.new_filename} → {rs.role}")
            except Exception as error:
                report.verdict, report.needs_human_review = "UNKNOWN", True
                report.notes.append(f"路由/注入失败：{type(error).__name__}")
                break
    audit.write_audit_results(results)
    rows = []
    for rs in routed:
        row = asdict(rs)
        row["new_path"], row["target_subdir"] = str(rs.new_path), str(rs.target_subdir)
        rows.append(json.dumps(row, ensure_ascii=False))
    atomic_write_text(_common.STATE_DIR / "routed.jsonl", "\n".join(rows) + ("\n" if rows else ""))
    if injected and not dry_run:
        inject.write_retrospective_log(injected, patches)
    n_pass = sum(report.is_approved for report in results)
    console.print(f"PASS={n_pass} review={len(results)-n_pass} injected={len(injected)}")
    return 0 if results and all(report.is_approved for report in results) else 1


def show_status() -> int:
    console = Console()
    records = _common.read_ledger()
    console.print(f"ledger: {_common.LEDGER_FILE}")
    for action in ("skill_file_copied", "frontmatter_patched", "frontmatter_restored", "skill_file_removed"):
        console.print(f"{action}: {sum(row.get('action')==action for row in records)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config")
    parser.add_argument("--vault")
    parser.add_argument("--dry-run", action="store_true", help="可调用模型并写暂存，不写最终技能或角色基因")
    parser.add_argument("--skip-extract", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--domain", default="se")
    args = parser.parse_args(argv)
    preflight.check(args.vault)
    _common.set_vault_root(args.vault)
    if args.status:
        return show_status()
    if not args.config:
        parser.error("需要 --config 或 --status")
    try:
        return run_pipeline(Path(args.config).resolve(), dry_run=args.dry_run,
                            skip_extract=args.skip_extract, domain=args.domain)
    except (OSError, ValueError) as error:
        Console().print(f"[red]配置/运行失败：{type(error).__name__}[/red]")
        return 1


if __name__ == "__main__":
    sys.exit(main())
