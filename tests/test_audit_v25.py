"""Offline v2.5 adapter checks; renderer is real, model calls are replaced."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import load_script


DIMENSIONS = ("coverage_status", "support_status", "synthesis_status", "source_inventory_status")


@pytest.fixture
def env(isolated, tmp_path, monkeypatch):
    _, _, config = isolated.import_skillmind()
    import skillmind.auditor as auditor
    import skillmind.renderer as renderer
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    stage = tmp_path / "stage"
    monkeypatch.setattr(config, "DRAFTS_DIR", drafts)
    monkeypatch.setattr(config, "load_config", lambda: {"vault_dir": str(tmp_path / "configured-vault")})
    mod = load_script("02_audit.py")
    raw = tmp_path / "raw.md"
    raw.write_text("Keep a recoverable copy before replacement.\n", encoding="utf-8")
    source = {"source_hash": hashlib.sha256(raw.read_bytes()).hexdigest(), "raw_path": str(raw), "doc_type": "skill", "source_url": "https://example.test/one"}
    return SimpleNamespace(mod=mod, config=config, auditor=auditor, renderer=renderer,
                           drafts=drafts, stage=stage, source=source, common=isolated)


def save(env, name="one", *, semantic=True, **changes):
    draft = {"uuid": name, "status": "draft", "source": dict(env.source), "meta": {"name": name},
             "schema_version": "semantic_distillation_v1", "extract_complete": True,
             "distillation": {
                 "source_spans": [{"id": "s1", "section": "metadata", "start_line": 1, "end_line": 1, "text": "Keep a recoverable copy before replacement."}],
                 "source_units": [{"id": "u1", "kind": "principle", "statement": "Keep a recoverable copy", "span_ids": ["s1"]}],
                 "ignored_spans": [], "uncovered": [],
                 "sections": [{"id": "c1", "title": "Recovery", "items": [{"id": "i1", "text": "Keep a recoverable copy before replacement.", "source_ids": ["u1"]}]}],
             }}
    if not semantic:
        draft.pop("schema_version")
        draft.pop("distillation")
        draft["source"]["doc_type"] = "blog"
    draft.update(changes)
    path = env.drafts / f"{name}.json"
    path.write_text(json.dumps(draft), encoding="utf-8")
    return path, draft


def report(env, monkeypatch, **changes):
    payload = dict(source_hash=env.source["source_hash"], verdict="PASS", needs_human_review=False,
                   coverage_weighted=None, extract_files=[str(p) for p in env.stage.glob("*.md")],
                   missing=[], hallucinations=[], items=[], prompt_version="semantic_audit_v2",
                   semantic_evidence={"synthesis": {"reason": "Supported and useful"}},
                   integrity_errors=[], evaluation_errors=[])
    payload.update({d: "PASS" for d in DIMENSIONS})
    payload.update(changes)
    obj = SimpleNamespace(**payload)
    obj.to_detailed_dict = lambda: dict(payload)
    calls = []
    monkeypatch.setattr(env.auditor, "audit_source", lambda *a, **kw: calls.append(kw) or obj)
    return obj, calls


def publish(env):
    return env.mod.publish_all_drafts([env.source], output_dir=env.stage)


def test_publish_is_scoped_uses_renderer_and_preserves_drafts(env):
    path, _ = save(env)
    unrelated = dict(env.source, source_hash="unrelated")
    save(env, "other", source=unrelated)
    before = path.read_bytes()
    assert publish(env) == 1
    assert path.read_bytes() == before
    assert len(list(env.stage.glob("*.md"))) == 1
    envelope = env.renderer.load_artifact_evidence(next(env.stage.glob("*.md")))
    assert envelope["draft"]["uuid"] == "one"
    assert not (Path(env.config.load_config()["vault_dir"]) / "skills").exists()


def test_publish_requires_explicit_scope(env):
    with pytest.raises(TypeError):
        env.mod.publish_all_drafts()


@pytest.mark.parametrize("changes", [{"extract_complete": False}, {"distillation": {}}, {"schema_version": "unsupported"}])
def test_incomplete_semantic_drafts_are_not_published(env, changes):
    save(env, **changes)
    assert publish(env) == 0


def test_semantic_nullable_pass_keeps_all_evidence_and_report(env, monkeypatch):
    paths = [save(env, name)[0] for name in ("one", "two")]
    assert publish(env) == 2
    _, calls = report(env, monkeypatch)
    result = env.mod.audit_one(env.source, "技术主管", vault_skills=env.stage)
    assert result.is_approved
    assert result.coverage is None
    assert result.schema_version == "semantic_distillation_v1"
    assert result.audit_prompt_version == "semantic_audit_v2"
    assert set(result.draft_paths) == {str(p) for p in paths}
    assert all(result.draft_sha256[str(p)] == hashlib.sha256(p.read_bytes()).hexdigest() for p in paths)
    assert len(result.artifact_paths) == 2
    assert calls[0]["max_extract_files"] == 2  # Derived from staged cards, never a fixed discovery cap.
    assert result.audit_details["semantic_evidence"]["synthesis"]["reason"] == "Supported and useful"


@pytest.mark.parametrize("fault", ["missing", "extra", "substitute", "tamper", "report_subset", "report_duplicate"])
def test_staging_and_report_require_exact_draft_correspondence(env, monkeypatch, fault):
    save(env)
    save(env, "two")
    publish(env)
    files = sorted(env.stage.glob("*.md"))
    if fault == "missing":
        files[1].unlink()
    elif fault == "extra":
        _, extra = save(env, "extra")
        (env.drafts / "extra.json").unlink()
        env.renderer.publish_to_vault(extra, env.config.load_config(), output_dir=env.stage)
    elif fault == "substitute":
        path = files[0].with_suffix(".evidence.json")
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["draft"]["meta"]["name"] = "Substituted content"
        path.write_text(json.dumps(payload), encoding="utf-8")
    elif fault == "tamper":
        files[0].write_text("tampered", encoding="utf-8")
    kwargs = {"extract_files": [str(files[0])]} if fault == "report_subset" else {}
    if fault == "report_duplicate":
        kwargs = {"extract_files": [str(files[0]), str(files[1]), str(files[0])]}
    report(env, monkeypatch, **kwargs)
    result = env.mod.audit_one(env.source, "技术主管", vault_skills=env.stage)
    assert result.verdict == "UNKNOWN"
    assert not result.is_approved
    assert result.notes


@pytest.mark.parametrize("changed", ["draft", "artifact", "sidecar"])
def test_changed_during_audit_is_unknown(env, monkeypatch, changed):
    draft, _ = save(env)
    publish(env)
    obj, _ = report(env, monkeypatch)
    artifact = next(env.stage.glob("*.md"))
    target = {"draft": draft, "artifact": artifact, "sidecar": artifact.with_suffix(".evidence.json")}[changed]
    def mutate(*a, **kw):
        target.write_text(target.read_text(encoding="utf-8") + " ", encoding="utf-8")
        return obj
    monkeypatch.setattr(env.auditor, "audit_source", mutate)
    result = env.mod.audit_one(env.source, "技术主管", vault_skills=env.stage)
    assert result.verdict == "UNKNOWN"
    assert not result.is_approved


@pytest.mark.parametrize("changes", [{"verdict": "INVALID"}, {"needs_human_review": "false"},
    {"support_status": "bad"}, {"coverage_status": "FAIL"}, {"evaluation_errors": ["invalid response"]}])
def test_malformed_or_inconsistent_pass_is_unknown(env, monkeypatch, changes):
    save(env)
    publish(env)
    report(env, monkeypatch, **changes)
    result = env.mod.audit_one(env.source, "技术主管", vault_skills=env.stage)
    assert result.verdict == "UNKNOWN"


def test_exception_is_persistable_unknown(env, monkeypatch):
    save(env)
    publish(env)
    def fail(*a, **kw):
        raise RuntimeError("provider unavailable")
    monkeypatch.setattr(env.auditor, "audit_source", fail)
    result = env.mod.audit_one(env.source, "技术主管", vault_skills=env.stage)
    assert result.verdict == "UNKNOWN"
    assert result.audit_details["adapter_errors"]


def test_semantic_failure_notes_keep_actual_reasons_without_scalar_threshold(env, monkeypatch):
    save(env)
    publish(env)
    report(env, monkeypatch, verdict="FAIL", needs_human_review=True, coverage_weighted=100,
           synthesis_status="FAIL", missing=[{"reason": "Restates source without synthesis"}])
    result = env.mod.audit_one(env.source, "技术主管", vault_skills=env.stage)
    assert result.verdict == "FAIL"
    assert "Restates source without synthesis" in " ".join(result.notes)
    assert "<" not in " ".join(result.notes)


def test_valid_semantic_fail_preserves_evaluation_errors_and_review_record(env, monkeypatch):
    save(env)
    publish(env)
    errors = ["source_id: judgments must match the exact expected id set once"]
    report(env, monkeypatch, verdict="FAIL", needs_human_review=True,
           support_status="FAIL", coverage_status="UNKNOWN", evaluation_errors=errors)
    result = env.mod.audit_one(env.source, "技术主管", vault_skills=env.stage)
    assert result.verdict == "FAIL"
    assert result.support_status == "FAIL"
    assert result.coverage_status == "UNKNOWN"
    assert result.audit_details["evaluation_errors"] == errors
    assert errors[0] in result.notes
    assert not result.is_approved
    env.mod.write_audit_results([result])
    saved = json.loads(env.common.REVIEW_FILE.read_text(encoding="utf-8"))
    assert saved["verdict"] == "FAIL"
    assert saved["audit_details"]["evaluation_errors"] == errors


@pytest.mark.parametrize("field", ["raw_path", "source_repo"])
def test_sidecar_reinsertion_of_sanitized_private_fields_is_unknown(env, monkeypatch, field):
    save(env)
    publish(env)
    sidecar = next(env.stage.glob("*.evidence.json"))
    envelope = json.loads(sidecar.read_text(encoding="utf-8"))
    envelope["draft"]["source"][field] = str(env.drafts / "private-local-provenance")
    sidecar.write_text(json.dumps(envelope), encoding="utf-8")
    report(env, monkeypatch)
    result = env.mod.audit_one(env.source, "技术主管", vault_skills=env.stage)
    assert result.verdict == "UNKNOWN"
    assert not result.is_approved


def test_staged_markdown_edit_and_rehashed_sidecar_cannot_authorize_approval(env, monkeypatch):
    save(env)
    publish(env)
    artifact = next(env.stage.glob("*.md"))
    content = artifact.read_text(encoding="utf-8")
    assert "Keep a recoverable copy before replacement." in content
    artifact.write_text(content.replace("Keep a recoverable copy before replacement.",
                                        "Delete all copies before replacement."), encoding="utf-8")
    sidecar = artifact.with_suffix(".evidence.json")
    envelope = json.loads(sidecar.read_text(encoding="utf-8"))
    envelope["artifact_sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    sidecar.write_text(json.dumps(envelope), encoding="utf-8")
    _, calls = report(env, monkeypatch)
    result = env.mod.audit_one(env.source, "技术主管", vault_skills=env.stage)
    assert result.verdict == "UNKNOWN"
    assert not result.is_approved
    assert calls == []


def test_raw_source_bytes_must_match_cached_source_hash_before_model_call(env, monkeypatch):
    save(env)
    publish(env)
    other = env.drafts.parent / "other-raw.md"
    other.write_text("A different source that must not inherit the cached audit.", encoding="utf-8")
    substituted_source = dict(env.source, raw_path=str(other))
    _, calls = report(env, monkeypatch)
    result = env.mod.audit_one(substituted_source, "技术主管", vault_skills=env.stage)
    assert result.verdict == "UNKNOWN"
    assert not result.is_approved
    assert calls == []


def test_legacy_non_skill_still_passes_with_verified_evidence(env, monkeypatch):
    save(env, semantic=False)
    env.source["doc_type"] = "blog"
    publish(env)
    report(env, monkeypatch, **{d: None for d in DIMENSIONS}, coverage_weighted=100)
    result = env.mod.audit_one(env.source, "技术主管", vault_skills=env.stage)
    assert result.is_approved
    assert result.schema_version == ""


def test_write_results_keeps_every_nonapproved_record_for_review(env):
    results = [env.common.AuditResult(str(i), "", "技术主管", verdict=state,
               needs_human_review=review) for i, (state, review) in enumerate([
                   ("PASS", False), ("PASS", True), ("FAIL", True), ("UNKNOWN", True)])]
    env.mod.write_audit_results(results)
    approved = [json.loads(line) for line in env.common.APPROVED_FILE.read_text(encoding="utf-8").splitlines()]
    review = [json.loads(line) for line in env.common.REVIEW_FILE.read_text(encoding="utf-8").splitlines()]
    assert len(approved) == 1
    assert len(review) == 3


@pytest.mark.parametrize("skip_extract", [False, True])
def test_cli_scopes_cache_and_retains_staging_evidence(env, monkeypatch, tmp_path, skip_extract):
    import yaml
    collector, _, _ = env.common.import_skillmind()
    save(env)
    cached = [env.source, dict(env.source, source_hash="unrelated", source_url="https://example.test/other")]
    monkeypatch.setattr(collector, "list_cached", lambda: cached)
    extracted, audited = [], []
    monkeypatch.setattr(env.mod, "extract_batch", lambda infos, **kw: extracted.extend(infos) or [
        {"source": info, "drafts": [{"extract_complete": True}]} for info in infos])
    def fake_audit(info, role, *, vault_skills):
        audited.append((info, Path(vault_skills)))
        return env.common.AuditResult(info["source_hash"], "", role, verdict="PASS", needs_human_review=False)
    monkeypatch.setattr(env.mod, "audit_one", fake_audit)
    path = tmp_path / "sources.yaml"
    path.write_text(yaml.safe_dump({"sources": [{"url": env.source["source_url"]}]}), encoding="utf-8")
    assert env.mod.main(["--config", str(path)] + (["--skip-extract"] if skip_extract else [])) == 0
    assert [item[0]["source_hash"] for item in audited] == [env.source["source_hash"]]
    assert [info["source_hash"] for info in extracted] == ([] if skip_extract else [env.source["source_hash"]])
    assert audited[0][1].is_dir()
    assert len(list(audited[0][1].glob("*.md"))) == 1


@pytest.mark.parametrize("state", ["FAIL", "UNKNOWN"])
def test_cli_returns_nonzero_and_keeps_failed_sources(env, monkeypatch, tmp_path, state):
    import yaml
    collector, _, _ = env.common.import_skillmind()
    monkeypatch.setattr(collector, "list_cached", lambda: [env.source])
    monkeypatch.setattr(env.mod, "audit_one", lambda info, role, **kw: env.common.AuditResult(info["source_hash"], "", role, verdict=state))
    path = tmp_path / "sources.yaml"
    path.write_text(yaml.safe_dump({"sources": [{"url": env.source["source_url"]}]}), encoding="utf-8")
    assert env.mod.main(["--config", str(path), "--skip-extract"]) != 0
    rows = [json.loads(line) for line in env.common.REVIEW_FILE.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["verdict"] == state


def test_cli_matches_configured_raw_skill_url_to_normalized_cached_blob(env, monkeypatch, tmp_path):
    import yaml
    collector, _, _ = env.common.import_skillmind()
    raw_url = "https://raw.githubusercontent.com/owner/repo/pinned/skills/tool/SKILL.md"
    blob_url = "https://github.com/owner/repo/blob/pinned/skills/tool/SKILL.md"
    cached = dict(env.source, source_url=blob_url, source_repo=blob_url)
    monkeypatch.setattr(collector, "list_cached", lambda: [cached])
    audited = []
    def fake_audit(info, role, **kw):
        audited.append(info)
        return env.common.AuditResult(info["source_hash"], "", role, verdict="PASS", needs_human_review=False)
    monkeypatch.setattr(env.mod, "audit_one", fake_audit)
    path = tmp_path / "sources.yaml"
    path.write_text(yaml.safe_dump({"sources": [{"url": raw_url}]}), encoding="utf-8")
    assert env.mod.main(["--config", str(path), "--skip-extract"]) == 0
    assert [info["source_hash"] for info in audited] == [cached["source_hash"]]


def test_uncovered_semantic_draft_is_not_published(env):
    _, draft = save(env)
    draft["distillation"]["source_units"].append({"id": "u2", "kind": "principle", "statement": "An omitted unit", "span_ids": ["s1"]})
    draft["distillation"]["uncovered"] = [{"source_id": "u2", "reason": "Omitted"}]
    save(env, distillation=draft["distillation"])
    assert publish(env) == 0
