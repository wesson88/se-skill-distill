"""Shared publication integrity and reversible file-state helpers.

All paths in transaction records are relative to the selected vault. Prior
bytes live in the private state ledger rather than a shared, overwriteable bak.
"""
from __future__ import annotations

import base64
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

import _common as common

SEMANTIC_SCHEMA = "semantic_distillation_v1"


def renderer_module():
    common.import_skillmind()
    from skillmind import renderer
    return renderer


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise ValueError("Expected a regular file")
    return sha256_bytes(path.read_bytes())


def source_hash(draft: dict) -> str:
    return str((draft.get("source") or {}).get("source_hash") or draft.get("source_hash") or "")


def canonical_json(data: dict) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False)


def evidence_draft(draft: dict) -> dict:
    # This is the same sanitizer used when SkillMind stages audited artifacts.
    return renderer_module()._published_evidence_draft(draft)


def component(value: str, label: str) -> str:
    if (not isinstance(value, str) or not value.strip() or value in (".", "..")
            or value != value.strip() or value.endswith(".")
            or re.search(r'[\\/:*?"<>|\x00-\x1f\x7f]', value)):
        raise ValueError(f"Invalid {label} path component")
    return value


def vault_path(path: Path | str, *, relative: bool = False) -> Path:
    root = common.VAULT_ROOT.resolve()
    candidate = Path(path)
    if relative:
        if candidate.is_absolute() or candidate.drive or ".." in candidate.parts:
            raise ValueError("Ledger path must be relative to the vault")
        candidate = root / candidate
    if not candidate.is_absolute() or candidate.is_symlink():
        raise ValueError("Expected a regular path within the vault")
    resolved = candidate.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError("Path escapes the selected vault")
    if candidate.exists() and not candidate.is_file():
        raise ValueError("Expected a file path")
    return candidate


def validate_destinations(routed) -> tuple[Path, Path]:
    component(routed.role, "role")
    component(routed.domain, "domain")
    component(routed.new_filename, "filename")
    if Path(routed.new_filename).suffix != ".md":
        raise ValueError("Skill filename must end in .md")
    expected = common.skill_subdir(routed.role, routed.domain)
    if Path(routed.target_subdir).resolve() != expected.resolve():
        raise ValueError("Target directory differs from the routed role/domain")
    skill = vault_path(routed.new_path)
    if skill.resolve() != (expected / routed.new_filename).resolve():
        raise ValueError("Skill path differs from the routed filename")
    sidecar = vault_path(skill.with_suffix(".evidence.json"))
    return skill, sidecar


def validate_semantic(draft: dict) -> None:
    if draft.get("schema_version") == SEMANTIC_SCHEMA:
        renderer_module()
        from skillmind.distillation import renderable_sections
        try:
            renderable_sections(draft)
        except RuntimeError as exc:
            raise ValueError("Invalid canonical semantic sections") from exc
        if draft.get("extract_complete") is not True or draft["distillation"]["uncovered"]:
            raise ValueError("Semantic extraction is incomplete")
    elif draft.get("schema_version"):
        raise ValueError("Unsupported draft schema")
    elif (draft.get("source") or {}).get("doc_type", draft.get("doc_type")) == "skill":
        raise ValueError("Skill draft requires the semantic schema")


def _bound_paths(paths: list, hashes: dict, label: str) -> dict[str, str]:
    if not isinstance(paths, list) or not paths or not isinstance(hashes, dict):
        raise ValueError(f"Missing bound {label} paths/hashes")
    if not all(isinstance(path, str) and path for path in paths):
        raise ValueError(f"Invalid {label} paths")
    resolved = [str(Path(path).resolve()) for path in paths]
    if len(resolved) != len(set(resolved)):
        raise ValueError(f"Duplicate {label} paths")
    normalized = {str(Path(path).resolve()): digest for path, digest in hashes.items()}
    if len(normalized) != len(hashes) or set(resolved) != set(normalized):
        raise ValueError(f"Incomplete bound {label} hash inventory")
    for path, digest in normalized.items():
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"Invalid {label} digest")
        if sha256_file(Path(path)) != digest:
            raise ValueError(f"Audited {label} bytes changed")
    return normalized


def validate_audit(routed, draft: dict) -> None:
    """Fail closed before writes, including approvals made by older versions."""
    if not isinstance(routed.audit, dict) or not routed.audit:
        raise ValueError("Missing bound audit record")
    audit = common.AuditResult(**routed.audit)
    if not audit.is_approved:
        raise ValueError("Audit is not approved")
    if audit.source_hash != source_hash(draft) or not audit.source_hash:
        raise ValueError("Audit source identity differs from draft")
    if audit.schema_version != draft.get("schema_version", ""):
        raise ValueError("Audit schema differs from draft")
    if not isinstance(audit.audit_details, dict) or audit.audit_details.get("source_sha256") != audit.source_hash:
        raise ValueError("Audit lacks a bound source digest")
    drafts = _bound_paths(audit.draft_paths, audit.draft_sha256, "draft")
    current_path = str(Path(routed.draft_path).resolve())
    if current_path not in drafts or str(Path(audit.draft_path).resolve()) != current_path:
        raise ValueError("Current card is not the audited draft")
    saved = json.loads(Path(current_path).read_text(encoding="utf-8"))
    if saved != draft:
        raise ValueError("Passed draft differs from the persisted audited card")
    expected = Counter()
    for path in drafts:
        card = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(card, dict) or source_hash(card) != audit.source_hash:
            raise ValueError("Audited draft belongs to another source")
        if card.get("schema_version", "") != audit.schema_version:
            raise ValueError("Audited cards mix schemas")
        validate_semantic(card)
        raw = (card.get("source") or {}).get("raw_path")
        if raw and sha256_file(Path(raw)) != audit.audit_details["source_sha256"]:
            raise ValueError("Raw source changed after audit")
        expected[canonical_json(evidence_draft(card))] += 1
    artifacts = _bound_paths(audit.artifact_paths, audit.artifact_sha256, "staging artifact")
    sidecar_paths = [str(Path(path).with_suffix(".evidence.json")) for path in artifacts]
    _bound_paths(sidecar_paths, audit.audit_details.get("staging_sidecar_sha256"), "staging sidecar")
    renderer = renderer_module()
    actual = Counter(canonical_json(renderer.load_artifact_evidence(path)["draft"]) for path in artifacts)
    if expected != actual:
        raise ValueError("Staging evidence differs from audited canonical drafts")


def snapshot(path: Path) -> dict:
    path = vault_path(path)
    data = path.read_bytes() if path.exists() else None
    return {"relpath": path.relative_to(common.VAULT_ROOT).as_posix(),
            "before_b64": base64.b64encode(data).decode("ascii") if data is not None else None,
            "before_sha256": sha256_bytes(data) if data is not None else None}


def decode_prior(record: dict) -> bytes | None:
    value = record["before_b64"]
    if value is None:
        if record.get("before_sha256") is not None:
            raise ValueError("Invalid absent-file snapshot")
        return None
    data = base64.b64decode(value, validate=True)
    if sha256_bytes(data) != record.get("before_sha256"):
        raise ValueError("Prior file snapshot hash mismatch")
    return data


def restore_snapshots(records: list[dict]) -> None:
    """Compensate writes using bytes, bypassing the failed text-write callback."""
    for record in reversed(records):
        path = vault_path(record["relpath"], relative=True)
        data = decode_prior(record)
        if data is None:
            path.unlink(missing_ok=True)
        else:
            common.atomic_write_bytes(path, data)


def complete_snapshots(records: list[dict]) -> list[dict]:
    return [dict(record, after_sha256=sha256_file(vault_path(record["relpath"], relative=True)))
            for record in records]


def assert_owned_pair(path: Path, draft: dict) -> None:
    sidecar = path.with_suffix(".evidence.json")
    if not path.exists() and not sidecar.exists():
        return
    if not path.is_file() or not sidecar.is_file():
        raise ValueError("Destination has a foreign or incomplete publication pair")
    envelope = renderer_module().load_artifact_evidence(path)
    if not draft.get("uuid") or envelope["draft"].get("uuid") != draft["uuid"]:
        raise ValueError("Destination belongs to another card")
    artifact_hash, evidence_hash = sha256_file(path), sha256_file(sidecar)
    relpath = path.relative_to(common.VAULT_ROOT).as_posix()
    records = common.read_ledger()
    rolled_back = {record.get("rollback_of_run") for record in records if record.get("action") == "publication_rolled_back"}
    for record in reversed(records):
        if (record.get("action") == "publication_committed" and record.get("relpath") == relpath
                and record.get("run_id") not in rolled_back):
            if record.get("artifact_sha256") == artifact_hash and record.get("evidence_sha256") == evidence_hash:
                return
            break
    raise ValueError("Destination has no matching generated ownership record or was edited")
