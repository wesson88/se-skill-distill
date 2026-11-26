"""Persisted SE trace/archive and dual-schema contracts, with no live providers."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import load_script
from test_audit_v25 import env, save, report, publish
from test_publication_v25 import publication, sha


def test_unmeasured_hallucination_rate_is_null(isolated):
    assert isolated.AuditResult("source", "", "role").hallucination_rate is None


def test_batch_forwards_identical_diagnostic_and_shared_run(isolated, tmp_path, monkeypatch):
    from skillmind.diagnostics import DistillationError, TraceRecorder, diagnostic_run
    mod = load_script("02_audit.py")
    captured = []
    def fail(info, console=None):
        recorder = TraceRecorder({"diagnostics_dir": str(tmp_path / "diagnostics")}, info["source_hash"])
        error = DistillationError("Missing unit u2", stage="contract", category="contract", validation_errors=["u2"])
        captured.append(recorder.failure(error))
        raise error
    monkeypatch.setattr(mod, "extract_one", fail)
    with diagnostic_run("outer-run"):
        result = mod.extract_batch([{"source_hash": "abc"}])[0]
    assert result["error"] == captured[0]
    assert result["run_id"] == "outer-run"
    event = tmp_path / "diagnostics" / result["error"]["artifact_ref"]
    assert hashlib.sha256(event.read_bytes()).hexdigest() == result["error"]["artifact_sha256"]
    assert json.loads(event.read_text())["validation_errors"] == ["u2"]


def test_unknown_batch_exception_gets_safe_persisted_diagnostic(isolated, tmp_path, monkeypatch):
    _, _, config = isolated.import_skillmind()
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    mod = load_script("02_audit.py")
    def fail(info, console=None):
        raise ValueError("Authorization: Bearer private-token at D:\\private\\input.md https://secret.test?token=private")
    monkeypatch.setattr(mod, "extract_one", fail)
    result = mod.extract_batch([{"source_hash": "abc"}])[0]
    assert isinstance(result["error"], dict)
    serialized = json.dumps(result)
    assert "private-token" not in serialized and "D:\\\\private" not in serialized
    assert result["error"]["stage"] == "extract"
    assert (config.CACHE_DIR / "diagnostics" / result["error"]["artifact_ref"]).is_file()


@pytest.mark.parametrize("available", [True, False])
def test_unexpected_exception_opaque_credentials_are_never_persisted(isolated, tmp_path, monkeypatch, available):
    _, _, config = isolated.import_skillmind()
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    secret = "opaque-fixture-credential-no-known-prefix"
    def configuration():
        if not available:
            raise OSError("private config unavailable")
        return {"llm_profiles": {"extract": {"api_key": secret}}}
    monkeypatch.setattr(config, "load_config", configuration)
    mod = load_script("02_audit.py")
    def fail(info, console=None):
        try:
            raise ValueError(secret)
        except ValueError as cause:
            raise RuntimeError(secret) from cause
    monkeypatch.setattr(mod, "extract_one", fail)
    result = mod.extract_batch([{"source_hash": "abc"}])[0]
    event = config.CACHE_DIR / "diagnostics" / result["error"]["artifact_ref"]
    assert secret not in json.dumps(result) + event.read_text()
    assert result["error"]["artifact_sha256"] == sha(event)


def test_archive_retains_previous_runs_and_errors(isolated):
    mod = load_script("02_audit.py")
    first = isolated.AuditResult("one", "", "role", audit_details={"extraction_error": {"stage": "contract", "artifact_ref": "one.json"}})
    mod.write_audit_results([first])
    before = {p: p.read_bytes() for p in (isolated.AUDIT_DIR / "runs").glob("*.jsonl")}
    assert len(before) == 1
    mod.write_audit_results([isolated.AuditResult("two", "", "role")])
    files = list((isolated.AUDIT_DIR / "runs").glob("*.jsonl"))
    assert len(files) == 2
    assert all(path.read_bytes() == data for path, data in before.items())
    rows = [json.loads(p.read_text()) for p in files]
    assert len({row["run_id"] for row in rows}) == 2
    assert next(row for row in rows if row["source_hash"] == "one")["audit_details"]["extraction_error"]["artifact_ref"] == "one.json"
    assert json.loads(isolated.REVIEW_FILE.read_text())["source_hash"] == "two"


def test_pipeline_failure_queue_keeps_trace_never_consumes_stale_drafts(isolated, tmp_path, monkeypatch):
    from skillmind.diagnostics import DistillationError, TraceRecorder
    _, _, config = isolated.import_skillmind()
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    pipeline = load_script("pipeline.py")
    audit = load_script("02_audit.py")
    source = {"source_hash": "abc", "raw_path": "unavailable", "doc_type": "skill"}
    request = {"kind": "local_dir", "path": "fixture", "target_role": "role"}
    cfg = tmp_path / "sources.yaml"
    cfg.write_text(json.dumps({"sources": [request]}))
    errors = []
    def fail(info, console=None):
        recorder = TraceRecorder({}, "abc")
        error = DistillationError("Missing disposition u9", stage="contract", category="contract")
        errors.append(recorder.failure(error))
        raise error
    monkeypatch.setattr(audit, "extract_one", fail)
    def forbidden(*args, **kwargs):
        pytest.fail("Failed extraction must not publish or audit stale drafts")
    monkeypatch.setattr(audit, "publish_all_drafts", forbidden)
    monkeypatch.setattr(audit, "audit_one", forbidden)
    stages = {"01_ingest.py": SimpleNamespace(ingest_batch=lambda *a, **kw: [(request, [source])]),
              "02_audit.py": audit, "03_route.py": SimpleNamespace(), "04_inject.py": SimpleNamespace()}
    monkeypatch.setattr(pipeline, "_load_stage", lambda name: stages[name])
    assert pipeline.run_pipeline(cfg) == 1
    row = json.loads(isolated.REVIEW_FILE.read_text())
    assert row["audit_details"]["extraction_error"] == errors[0]
    assert row["run_id"] == errors[0]["run_id"]
    assert row["coverage"] is None and row["hallucination_rate"] is None
    archive = next((isolated.AUDIT_DIR / "runs").glob("*.jsonl"))
    assert json.loads(archive.read_text()) == row


@pytest.mark.parametrize("state", ["UNKNOWN", "FAIL", "PASS"])
def test_semantic_rate_requires_complete_claim_measurement(env, monkeypatch, state):
    _, draft = save(env)
    if state == "FAIL":
        draft["distillation"]["sections"][0]["items"].append({"id": "i2", "text": "Keep another copy.", "source_ids": ["u1"]})
        save(env, **draft)
    publish(env)
    from skillmind.semantic_auditor import _claim_ids
    expected = len(_claim_ids([draft]))
    report(env, monkeypatch, verdict=state, needs_human_review=state != "PASS", support_status=state,
           n_supported=expected - 1 if state == "FAIL" else expected, n_unsupported=1 if state == "FAIL" else 0,
           n_claims_unknown=1 if state == "UNKNOWN" else 0)
    result = env.mod.audit_one(env.source, "role", vault_skills=env.stage)
    assert result.hallucination_rate == (None if state == "UNKNOWN" else 1 / expected if state == "FAIL" else 0.0)


def test_partial_claim_counts_cannot_be_displayed_as_measured(env, monkeypatch):
    _, draft = save(env)
    draft["distillation"]["sections"][0]["items"].append({"id": "i2", "text": "Keep another copy.", "source_ids": ["u1"]})
    save(env, **draft)
    publish(env)
    report(env, monkeypatch, n_supported=1, n_unsupported=0, n_claims_unknown=0, support_status="UNKNOWN",
           verdict="UNKNOWN", needs_human_review=True)
    result = env.mod.audit_one(env.source, "role", vault_skills=env.stage)
    assert result.hallucination_rate is None


@pytest.mark.parametrize("extraction", [[], [{"drafts": [], "source": {}}], [{"drafts": [{"extract_complete": False}], "source": {}}]])
def test_standalone_incomplete_extraction_never_consumes_stale_draft(env, monkeypatch, tmp_path, extraction):
    collector, _, _ = env.common.import_skillmind()
    save(env)
    monkeypatch.setattr(collector, "list_cached", lambda: [env.source])
    monkeypatch.setattr(env.mod, "extract_batch", lambda *a, **kw: extraction)
    def forbidden(*a, **kw):
        pytest.fail("Incomplete extraction must not audit the stale draft")
    monkeypatch.setattr(env.mod, "audit_one", forbidden)
    config = tmp_path / "sources.yaml"
    config.write_text(json.dumps({"sources": [{"url": env.source["source_url"]}]}))
    assert env.mod.main(["--config", str(config)]) == 1
    row = json.loads(env.common.REVIEW_FILE.read_text())
    assert row["verdict"] == "UNKNOWN"
    assert row["audit_details"]["extraction_status"] == "incomplete"
    assert not list((env.common.AUDIT_DIR / "staging").rglob("*.md"))


def test_provider_contract_failure_cache_queue_and_events_share_exact_envelope(isolated, tmp_path, monkeypatch):
    import litellm
    from skillmind import config, distillation
    cfg = {"llm": {"model": "openai/offline-fixture", "api_key": "offline-fixture",
                    "max_retries": 0, "qpm": 1000000000},  # Replay pacing only; no quality threshold.
           "diagnostics_dir": str(tmp_path / "diagnostics")}
    monkeypatch.setattr(config, "load_config", lambda: cfg)
    monkeypatch.setattr(distillation, "EXTRACT_CACHE_DIR", tmp_path / "extract-cache")
    raw = tmp_path / "source.md"
    raw.write_text("Purpose and recovery before replacement.\n", encoding="utf-8")
    source = {"source_hash": sha(raw), "raw_path": str(raw), "doc_type": "skill"}
    inventory = {"units": [{"kind": "purpose", "statement": "Purpose", "span_ids": ["p0001"]},
                           {"kind": "principle", "statement": "Recovery", "span_ids": ["p0001"]}], "ignored_spans": []}
    synthesis = {"meta": {"name": "Recovery", "intent": "Retain recovery", "trigger_keywords": ["replacement"]},
                 "sections": [{"title": "Recovery", "items": [{"text": "Retain recovery.", "source_ids": ["u0002"]}]}],
                 "excluded": [], "uncovered": []}
    responses = iter((inventory, synthesis))
    def replay(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content=json.dumps(next(responses))))],
                               usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2))
    monkeypatch.setattr(litellm, "completion", replay)
    pipeline, audit = load_script("pipeline.py"), load_script("02_audit.py")
    request = {"kind": "local_dir", "path": "fixture", "target_role": "role"}
    source_config = tmp_path / "sources.yaml"
    source_config.write_text(json.dumps({"sources": [request]}))
    stages = {"01_ingest.py": SimpleNamespace(ingest_batch=lambda *a, **kw: [(request, [source])]),
              "02_audit.py": audit, "03_route.py": SimpleNamespace(), "04_inject.py": SimpleNamespace()}
    monkeypatch.setattr(pipeline, "_load_stage", lambda name: stages[name])
    assert pipeline.run_pipeline(source_config) == 1
    queued = json.loads(isolated.REVIEW_FILE.read_text())
    cached = json.loads(next((tmp_path / "extract-cache").glob("*.json")).read_text())
    error = queued["audit_details"]["extraction_error"]
    assert error == cached["error"]
    assert error["stage"] == "synthesis-validate" and "u0001" in json.dumps(error["validation_errors"])
    assert queued["run_id"] == error["run_id"]
    event = tmp_path / "diagnostics" / error["artifact_ref"]
    assert sha(event) == error["artifact_sha256"]
    assert len(list((tmp_path / "diagnostics").rglob("*.json"))) == 3  # Inventory, synthesis, final failure.


def v2_draft(draft):
    draft = copy.deepcopy(draft)
    draft["schema_version"] = "semantic_distillation_v2"
    payload = draft["distillation"]
    text = "Thanks to upstream authors."
    payload["source_spans"].append({"id": "s-credit", "section": "Credits", "start_line": 2, "end_line": 2, "text": text})
    payload["source_units"].append({"id": "u-credit", "kind": "concept", "statement": text, "span_ids": ["s-credit"]})
    payload["excluded"] = [{"source_id": "u-credit", "reason_code": "attribution", "span_ids": ["s-credit"], "source_quote": text, "reason": "Acknowledgment only"}]
    return draft


def test_v2_stages_audits_and_retains_exclusions_outside_body(env, monkeypatch):
    _, draft = save(env)
    draft = v2_draft(draft)
    save(env, **draft)
    assert publish(env) == 1
    report(env, monkeypatch, n_excluded=1, coverage_weighted=None,
           semantic_evidence={"exclusions": [{"source_id": "one:u-credit", "status": "unknown", "reason": "unverified"}]},
           verdict="UNKNOWN", needs_human_review=True, source_inventory_status="UNKNOWN")
    result = env.mod.audit_one(env.source, "role", vault_skills=env.stage)
    assert result.schema_version == "semantic_distillation_v2"
    assert result.coverage is None and not result.is_approved
    artifact = next(env.stage.glob("*.md"))
    assert "Thanks to upstream authors." not in artifact.read_text()
    assert env.renderer.load_artifact_evidence(artifact)["draft"]["distillation"]["excluded"] == draft["distillation"]["excluded"]


@pytest.mark.parametrize("inventory", ["PASS", "FAIL", "UNKNOWN"])
@pytest.mark.parametrize("proof", ["whole_unit", "legacy", "mixed", "fragment", "missing"])
def test_v2_publication_requires_four_dimensions_and_keeps_original_binding(publication, inventory, proof):
    p = publication
    p.draft = v2_draft(p.draft)
    p.path.write_text(json.dumps(p.draft))
    p.stage = p.renderer.publish_to_vault(p.draft, {"se": True}, output_dir=p.stage.parent)
    sidecar = p.stage.with_suffix(".evidence.json")
    p.routed.audit.update(schema_version=p.draft["schema_version"], source_inventory_status=inventory,
                         draft_sha256={str(p.path): sha(p.path)}, artifact_paths=[str(p.stage)], artifact_sha256={str(p.stage): sha(p.stage)})
    p.routed.audit["audit_details"]["staging_sidecar_sha256"] = {str(sidecar): sha(sidecar)}
    row = {"source_id": p.draft["uuid"] + ":u-credit", "status": "justified",
           "scope": "whole_unit", "unit_content": "attribution_only", "reason": "Entire unit is upstream attribution."}
    evidence = {"exclusions": [row], "attribution_check": {"status": "PASS", "reason": "No credits in published fields."}}
    if proof == "legacy":
        row.pop("scope"); row.pop("unit_content")
    elif proof == "mixed":
        row["unit_content"] = "mixed"
    elif proof == "fragment":
        row["scope"] = "source_quote"
    elif proof == "missing":
        evidence = {}
    p.routed.audit["audit_details"]["semantic_evidence"] = evidence
    result = p.mod.inject_one(p.routed, p.draft)
    accepted = inventory == "PASS" and proof == "whole_unit"
    assert result["ok"] == accepted, result
    if not accepted:
        assert not p.routed.new_path.exists()
    if accepted:
        text = p.routed.new_path.read_text(encoding="utf-8")
        assert "semantic_distillation_v2" in text
        assert "Thanks to upstream authors." not in text
        assert p.renderer.load_artifact_evidence(p.routed.new_path)["draft"]["distillation"]["excluded"]


def test_preflight_accepts_both_schema_contracts():
    assert load_script("preflight.py")._check_skillmind()[0] == "ok"


def test_preflight_rejects_upstream_without_whole_unit_approval_contract(monkeypatch):
    from skillmind import semantic_auditor
    monkeypatch.delattr(semantic_auditor, "validate_exclusion_approval")
    assert load_script("preflight.py")._check_skillmind()[0] == "fail"


@pytest.mark.parametrize("fault", ["true_uncovered", "declared_v1", "unknown_schema"])
def test_v2_admission_never_reinterprets_uncovered_or_declared_schema(env, fault):
    _, draft = save(env)
    draft = v2_draft(draft)
    if fault == "true_uncovered":
        draft["distillation"]["source_units"].append({"id": "u-missing", "kind": "principle", "statement": "Missing method", "span_ids": ["s1"]})
        draft["distillation"]["uncovered"] = [{"source_id": "u-missing", "reason": "Not distilled"}]
    elif fault == "declared_v1":
        draft["schema_version"] = "semantic_distillation_v1"
    else:
        draft["schema_version"] = "semantic_distillation_future"
    save(env, **draft)
    assert publish(env) == 0


def test_valid_fail_survives_independent_malformed_dimension(env, monkeypatch):
    save(env)
    publish(env)
    report(env, monkeypatch, verdict="FAIL", needs_human_review=True, support_status="FAIL", coverage_status="malformed")
    result = env.mod.audit_one(env.source, "role", vault_skills=env.stage)
    assert result.verdict == "FAIL"
    assert result.support_status == "FAIL" and result.coverage_status == "UNKNOWN"
    assert result.audit_details["adapter_errors"]


def test_audit_exception_is_sanitized_before_queue_and_archive(env, monkeypatch):
    save(env)
    publish(env)
    def fail(*a, **kw):
        raise ValueError("Authorization: Bearer private-audit-secret D:\\private\\audit.md")
    monkeypatch.setattr(env.auditor, "audit_source", fail)
    result = env.mod.audit_one(env.source, "role", vault_skills=env.stage)
    env.mod.write_audit_results([result])
    for path in [env.common.REVIEW_FILE, *list((env.common.AUDIT_DIR / "runs").glob("*.jsonl"))]:
        text = path.read_text()
        assert "private-audit-secret" not in text and "D:\\\\private" not in text
        assert json.loads(text)["audit_details"]["audit_error"]["stage"] == "audit"
