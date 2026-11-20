"""Scoped SkillMind extraction, staging and conservative v2.5 audit adapter."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, is_dataclass
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile

import _common as common
from _common import AuditResult, ensure_skillmind_venv, import_skillmind

SEMANTIC_SCHEMA = "semantic_distillation_v1"
DIMENSIONS = ("coverage_status", "support_status", "synthesis_status", "source_inventory_status")
STATUSES = frozenset(("PASS", "FAIL", "UNKNOWN"))


def extract_one(source_info: dict, console=None) -> list[dict]:
    ensure_skillmind_venv()
    _, extractor, config = import_skillmind()
    return extractor.extract_skill(source_info["raw_path"], source_info, config.load_config(), console=console)


def extract_batch(source_infos: list[dict], console=None) -> list[dict]:
    results = []
    for info in source_infos:
        try:
            results.append({"source": info, "drafts": extract_one(info, console=console)})
        except Exception as exc:
            results.append({"source": info, "drafts": [], "error": f"Extraction failed ({type(exc).__name__})"})
    return results


def _source_hash(draft: dict) -> str:
    source = draft.get("source")
    nested = source.get("source_hash") if isinstance(source, dict) else None
    return str(nested or draft.get("source_hash") or "")


def _drafts_for(source_hash: str, config) -> list[tuple[Path, dict]]:
    found = []
    for path in sorted(Path(config.DRAFTS_DIR).glob("*.json")):
        try:
            draft = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(draft, dict) and _source_hash(draft) == source_hash:
            found.append((path.resolve(), draft))
    return found


def _validate_draft(draft: dict, source_info: dict) -> None:
    from skillmind.distillation import validate_distillation
    schema = draft.get("schema_version", "")
    source = draft.get("source") or {}
    if not isinstance(source, dict):
        raise ValueError("Draft source must be an object")
    doc_type = source.get("doc_type", draft.get("doc_type", source_info.get("doc_type", "skill")))
    if schema and schema != SEMANTIC_SCHEMA:
        raise ValueError("Unsupported draft schema")
    if schema == SEMANTIC_SCHEMA or doc_type == "skill":
        if schema != SEMANTIC_SCHEMA or draft.get("extract_complete") is not True:
            raise ValueError("Semantic extraction is incomplete or lacks its canonical schema")
        errors = validate_distillation(draft.get("distillation"))
        if errors:
            raise ValueError("Invalid semantic draft: " + "; ".join(errors))
        if draft["distillation"]["uncovered"]:
            raise ValueError("Semantic draft still contains uncovered source units")
        if not isinstance(draft.get("uuid"), str) or not draft["uuid"].strip():
            raise ValueError("Semantic draft requires a UUID")


def publish_all_drafts(source_infos: list[dict], console=None, *, output_dir=None) -> int:
    """Render requested source cards without modifying persisted drafts.

    The pipeline passes per-run staging. The default preserves the configured
    vault/skills API; the standalone CLI always uses dedicated staging.
    """
    _, _, config = import_skillmind()
    from skillmind.renderer import publish_to_vault
    cfg = config.load_config()
    destination = Path(output_dir).resolve() if output_dir is not None else config.get_vault_dir(cfg) / "skills"
    sources = {str(s["source_hash"]): s for s in source_infos if s.get("source_hash")}
    published = 0
    for source_hash, source in sources.items():
        for _, draft in _drafts_for(source_hash, config):
            try:
                _validate_draft(draft, source)
                publish_to_vault(draft, cfg, output_dir=destination)
                published += 1
            except Exception as exc:
                if console:
                    console.print(f"[yellow]Draft not staged ({type(exc).__name__})[/yellow]")
    return published


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(draft: dict, renderer) -> str:
    # Use SkillMind's exact publication sanitizer, retaining original status.
    return json.dumps(renderer._published_evidence_draft(draft), sort_keys=True, ensure_ascii=False)


def _artifact_source(path: Path, envelope: dict | None) -> str:
    """Use both channels so damaged Markdown still enters integrity checks."""
    if envelope and isinstance(envelope.get("draft"), dict):
        return _source_hash(envelope["draft"])
    import yaml
    try:
        text = path.read_text(encoding="utf-8")
        if text.startswith("---\n"):
            frontmatter = yaml.safe_load(text.split("---", 2)[1])
            if isinstance(frontmatter, dict):
                return str(frontmatter.get("source_hash") or "")
    except (OSError, ValueError, yaml.YAMLError):
        pass
    return ""


def _verified_artifacts(source_hash: str, root: Path, drafts: list[tuple[Path, dict]], renderer, cfg: dict):
    # Bind the actual published metadata and body to the persisted draft too.
    # A sidecar digest can be recomputed after editing Markdown, so its hash
    # alone cannot establish that the renderer produced the staged content.
    expected = Counter((_canonical(draft, renderer), renderer.render_to_markdown(draft, cfg))
                       for _, draft in drafts)
    actual = Counter()
    artifacts, sidecars = {}, {}
    for path in sorted(root.rglob("*.md")):
        sidecar = path.with_suffix(".evidence.json")
        try:
            envelope = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            envelope = None
        side_hash = _artifact_source(path, envelope if isinstance(envelope, dict) else None)
        markdown_hash = _artifact_source(path, None)
        if source_hash not in (side_hash, markdown_hash):
            continue
        if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink() or sidecar.is_symlink():
            raise ValueError("Staged evidence must be regular files within staging")
        verified = renderer.load_artifact_evidence(path)
        if _source_hash(verified["draft"]) != source_hash or markdown_hash != source_hash:
            raise ValueError("Staged artifact source identity differs from canonical draft")
        # The sidecar is already the renderer's canonical published snapshot.
        # Re-sanitizing it would conceal fields inserted after publication.
        actual[(json.dumps(verified["draft"], sort_keys=True, ensure_ascii=False),
                path.read_text(encoding="utf-8"))] += 1
        artifacts[str(path.resolve())] = _sha(path)
        sidecars[str(sidecar.resolve())] = _sha(sidecar)
    if expected != actual:
        missing = sum((expected - actual).values())
        extra = sum((actual - expected).values())
        raise ValueError(f"Staged evidence differs from persisted drafts: missing={missing}, extra/substituted={extra}")
    if not artifacts:
        raise ValueError("No verified staged artifacts for source")
    return artifacts, sidecars


def _details(report) -> dict:
    if hasattr(report, "to_detailed_dict"):
        return report.to_detailed_dict()
    if is_dataclass(report):
        return asdict(report)
    return {key: value for key, value in vars(report).items() if not callable(value)}


def _reason_notes(value) -> list[str]:
    reasons = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"reason", "description", "notes"} and isinstance(item, str) and item:
                reasons.append(item)
            elif key in {"integrity_errors", "evaluation_errors"} and isinstance(item, list):
                reasons.extend(str(error) for error in item)
            else:
                reasons.extend(_reason_notes(item))
    elif isinstance(value, list):
        for item in value:
            reasons.extend(_reason_notes(item))
    return list(dict.fromkeys(reasons))


def audit_one(source_info: dict, role: str, *, vault_skills=None) -> AuditResult:
    """Require exact source/card/artifact correspondence before approval."""
    result = AuditResult(source_info.get("source_hash", ""), "", role)
    try:
        ensure_skillmind_venv()
        _, _, config = import_skillmind()
        import skillmind.auditor as auditor
        import skillmind.renderer as renderer
        cfg = config.load_config()
        source_hash = result.source_hash
        if not source_hash:
            raise ValueError("Missing source hash")
        raw = Path(source_info["raw_path"])
        if not raw.is_file():
            raise ValueError("Source raw file is unavailable")
        raw_hash = _sha(raw)
        if raw_hash != source_hash:
            raise ValueError("Raw source bytes do not match the cached source hash")
        root = Path(vault_skills).resolve() if vault_skills is not None else config.get_vault_dir(cfg) / "skills"
        drafts = _drafts_for(source_hash, config)
        result.draft_paths = [str(path) for path, _ in drafts]
        result.draft_path = result.draft_paths[0] if result.draft_paths else ""
        result.draft_sha256 = {str(path): _sha(path) for path, _ in drafts}
        if not drafts:
            raise ValueError("No persisted source drafts")
        for _, draft in drafts:
            _validate_draft(draft, source_info)
        schemas = {draft.get("schema_version", "") for _, draft in drafts}
        if len(schemas) != 1:
            raise ValueError("Source drafts mix incompatible schemas")
        result.schema_version = schemas.pop()
        artifacts, sidecars = _verified_artifacts(source_hash, root, drafts, renderer, cfg)
        result.artifact_paths = list(artifacts)
        result.artifact_sha256 = artifacts
        result.audit_details = {"staging_sidecar_sha256": sidecars, "source_sha256": raw_hash}
        report = auditor.audit_source(source_hash, cfg=cfg, console=None,
                                      max_extract_files=len(artifacts),
                                      vault_skills_override=str(root))
        result.audit_details.update(_details(report))
        result.audit_details["staging_sidecar_sha256"] = sidecars
        result.audit_details["source_sha256"] = raw_hash
        result.audit_prompt_version = getattr(report, "prompt_version", "")
        result.missing = getattr(report, "missing", [])
        result.notes = _reason_notes(result.audit_details)
        after = _drafts_for(source_hash, config)
        if {str(path): _sha(path) for path, _ in after} != result.draft_sha256:
            raise ValueError("Persisted drafts changed during audit")
        after_artifacts, after_sidecars = _verified_artifacts(source_hash, root, after, renderer, cfg)
        if after_artifacts != artifacts or after_sidecars != sidecars or _sha(raw) != raw_hash:
            raise ValueError("Source or staged artifacts changed during audit")
        files = getattr(report, "extract_files", None)
        if not isinstance(files, list) or not all(isinstance(path, str) for path in files):
            raise ValueError("Audit report lacks a valid artifact list")
        reported = [str(Path(path).resolve()) for path in files]
        if Counter(reported) != Counter(artifacts.keys()):
            raise ValueError("Audit report artifact set is missing, extra or substituted")
        if getattr(report, "source_hash", None) != source_hash:
            raise ValueError("Audit report belongs to a different source")
        verdict = getattr(report, "verdict", None)
        review = getattr(report, "needs_human_review", None)
        if verdict not in STATUSES or type(review) is not bool:
            raise ValueError("Malformed audit verdict or review flag")
        if (verdict == "PASS") != (review is False):
            raise ValueError("Audit verdict and review flag are inconsistent")
        if not isinstance(result.missing, list):
            raise ValueError("Malformed missing-source report")
        coverage = getattr(report, "coverage_weighted", None)
        # SkillMind's API documents this display quantity on a 0–100 percent scale.
        if coverage is not None and (type(coverage) not in (float, int) or not math.isfinite(coverage) or not 0 <= coverage <= 100):
            raise ValueError("Malformed display coverage")
        result.coverage = coverage
        hallucinations = getattr(report, "hallucinations", [])
        items = getattr(report, "items", [])
        if not isinstance(hallucinations, list) or not isinstance(items, list):
            raise ValueError("Malformed audit item lists")
        result.hallucination_rate = len(hallucinations) / max(len(items), 1)
        if result.schema_version == SEMANTIC_SCHEMA:
            for name in DIMENSIONS:
                status = getattr(report, name, None)
                if status not in STATUSES:
                    raise ValueError(f"Malformed semantic dimension: {name}")
                setattr(result, name, status)
            if result.audit_details.get("integrity_errors"):
                raise ValueError("Semantic audit contains integrity errors")
            proven_failure = verdict == "FAIL" and any(getattr(result, name) == "FAIL" for name in DIMENSIONS)
            if result.audit_details.get("evaluation_errors") and not proven_failure:
                raise ValueError("Semantic audit contains unresolved evaluation errors")
            if verdict == "PASS" and (not all(getattr(result, name) == "PASS" for name in DIMENSIONS) or result.missing):
                raise ValueError("Semantic PASS lacks complete passing dimensions")
        result.verdict = verdict
        result.needs_human_review = review
    except Exception as exc:
        result.verdict = "UNKNOWN"
        result.needs_human_review = True
        # Provider/IO exceptions may echo credentials, so only controlled local reasons are copied.
        reason = str(exc) if isinstance(exc, ValueError) else f"Audit failed ({type(exc).__name__})"
        result.notes.append(reason)
        result.audit_details.setdefault("adapter_errors", []).append(reason)
    return result


def _find_draft_path(source_hash: str) -> str:
    _, _, config = import_skillmind()
    drafts = _drafts_for(source_hash, config)
    return str(drafts[0][0]) if drafts else ""


def write_audit_results(results: list[AuditResult]) -> None:
    """Every source is retained; explicit approvals alone enter approved.jsonl."""
    approved = [result for result in results if result.is_approved]
    review = [result for result in results if not result.is_approved]
    for path, items in ((common.APPROVED_FILE, approved), (common.REVIEW_FILE, review), (common.REJECT_FILE, [])):
        common.atomic_write_text(path, "".join(json.dumps(item.to_dict(), ensure_ascii=False) + "\n" for item in items))


def _infer_role_from_source(source: dict) -> str:
    role = source.get("target_role")
    if role and role != "auto":
        return role
    return common.detect_role_from_text((source.get("url") or source.get("source_url") or "") + " " + (source.get("notes") or "")) or "后端工程师"


def main(argv: list[str] | None = None) -> int:
    import argparse
    from rich.console import Console
    import yaml
    from source_types import is_github_skill_url, skill_collector_url
    parser = argparse.ArgumentParser(description="Scoped extract, stage and audit")
    parser.add_argument("--config", required=True)
    parser.add_argument("--role")
    parser.add_argument("--skip-extract", action="store_true")
    parser.add_argument("--skip-publish", action="store_true", help="Audit configured skills with strict evidence checks")
    args = parser.parse_args(argv)
    console = Console()
    config_data = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    collector, _, config = import_skillmind()
    cached = collector.list_cached()
    source_infos, unresolved = [], []
    for source in config_data.get("sources", []):
        target = source.get("url") or source.get("path", "")
        aliases = {target}
        if is_github_skill_url(target):
            aliases.add(skill_collector_url(target))
        matches = [entry for entry in cached if target and aliases.intersection(
                   (entry.get("source_url"), entry.get("source_repo"), entry.get("raw_path")))]
        if not matches:
            unresolved.append(AuditResult("", "", args.role or _infer_role_from_source(source),
                                          notes=["Configured source has no cached match"]))
        for match in matches:
            info = dict(match)
            for field in ("target_role", "notes", "url"):
                if field in source:
                    info[field] = source[field]
            if not any(item.get("source_hash") == info.get("source_hash") for item in source_infos):
                source_infos.append(info)
    extracted = [] if args.skip_extract else extract_batch(source_infos, console=console)
    failures = {item["source"].get("source_hash"): item["error"] for item in extracted if item.get("error")}
    results = list(unresolved)
    if args.skip_publish:
        stage = config.get_vault_dir(config.load_config()) / "skills"
    else:
        # Retain hashed evidence for later review and routing; never stage in the vault.
        stage_parent = common.AUDIT_DIR / "staging"
        stage_parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix="run-", dir=stage_parent))
        publish_all_drafts([info for info in source_infos if info.get("source_hash") not in failures], console=console, output_dir=stage)
    for info in source_infos:
        role = args.role or _infer_role_from_source(info)
        error = failures.get(info.get("source_hash"))
        result = AuditResult(info.get("source_hash", ""), "", role, notes=[error]) if error else audit_one(info, role, vault_skills=stage)
        results.append(result)
        console.print(f"coverage={common.format_coverage(result.coverage)} verdict={result.verdict} needs_review={result.needs_human_review}")
    write_audit_results(results)
    return 0 if results and all(result.is_approved for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
