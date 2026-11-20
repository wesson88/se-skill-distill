"""Offline publication integrity, compensation and edit-safe rollback contracts."""
import hashlib
import json
import copy
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from conftest import load_script


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def publication(isolated, tmp_path):
    isolated.import_skillmind()
    from skillmind import renderer
    raw = tmp_path / "source.md"
    raw.write_text("Keep the original before replacement.\n", encoding="utf-8")
    source_hash = sha(raw)
    draft = {
        "uuid": "publication-card", "status": "draft", "extract_complete": True,
        "schema_version": "semantic_distillation_v1",
        "source": {"source_hash": source_hash, "raw_path": str(raw), "source_repo": str(tmp_path),
                   "source_url": "https://example.test/source", "doc_type": "skill"},
        "meta": {"name": "Recovery contract", "trigger_keywords": ["replacement"]},
        "learning_enhancement": {"plain_summary": "Do not synthesize a new summary"},
        "distillation": {
            "source_spans": [{"id": "s1", "section": "metadata", "start_line": 1,
                              "end_line": 1, "text": "Keep the original before replacement."}],
            "source_units": [{"id": "u1", "kind": "principle", "statement": "Keep original", "span_ids": ["s1"]}],
            "ignored_spans": [], "uncovered": [],
            "sections": [{"id": "c1", "title": "Retain before replacing", "items": [
                {"id": "i1", "text": "Preserve the original bytes.\n\n```python\nrestore(original)\n```", "source_ids": ["u1"]}]},
                {"id": "c2", "title": "Recover after failure", "items": [
                    {"id": "i2", "text": "Restore every member of the publication pair.", "source_ids": ["u1"]}]}],
        },
    }
    path = tmp_path / "draft.json"
    path.write_text(json.dumps(draft), encoding="utf-8")
    stage = renderer.publish_to_vault(draft, {"vault_dir": str(tmp_path / "unused")}, output_dir=tmp_path / "stage")
    audit = isolated.AuditResult(source_hash, str(path), "技术主管", verdict="PASS", needs_human_review=False,
        schema_version=draft["schema_version"], audit_prompt_version="semantic_audit_v2",
        draft_paths=[str(path)], draft_sha256={str(path): sha(path)},
        artifact_paths=[str(stage)], artifact_sha256={str(stage): sha(stage)},
        audit_details={"staging_sidecar_sha256": {str(stage.with_suffix('.evidence.json')): sha(stage.with_suffix('.evidence.json'))},
                       "source_sha256": source_hash},
        **{name: "PASS" for name in ("coverage_status", "support_status", "synthesis_status", "source_inventory_status")})
    target = isolated.skill_subdir("技术主管")
    routed = isolated.RoutedSkill(str(path), "技术主管", "TL", 1, "TL1-recovery.md",
                                 target / "TL1-recovery.md", target, audit=audit.to_dict())
    return SimpleNamespace(common=isolated, mod=load_script("04_inject.py"), rollback=load_script("05_rollback.py"),
                           routed=routed, draft=draft, path=path, stage=stage, raw=raw, renderer=renderer)


def test_semantic_body_preserves_canonical_sections_only(publication):
    p = publication
    text = p.mod.render_vault_skill(p.routed, p.draft)
    for section in p.draft["distillation"]["sections"]:
        assert text.count("## " + section["title"]) == 1
        for item in section["items"]:
            assert text.count(item["text"]) == 1
    assert "Do not synthesize a new summary" not in text
    assert "## 1. 核心约束" not in text
    assert 'source_url: "https://example.test/source"' in text


def test_invalid_semantic_draft_is_rejected_by_renderer(publication):
    p = publication
    p.draft["distillation"]["sections"][0]["items"][0]["source_ids"] = ["missing"]
    with pytest.raises(ValueError):
        p.mod.render_vault_skill(p.routed, p.draft)


def test_injection_writes_bound_sanitized_pair_without_creating_role_gene(publication):
    p = publication
    result = p.mod.inject_one(p.routed, p.draft)
    assert result["ok"], result
    envelope = p.renderer.load_artifact_evidence(p.routed.new_path)
    assert envelope["artifact_sha256"] == sha(p.routed.new_path)
    assert envelope["artifact_sha256"] != sha(p.stage)
    assert "raw_path" not in envelope["draft"]["source"]
    assert "source_repo" not in envelope["draft"]["source"]
    assert not p.common.role_gene_file(p.routed.role).exists()
    data = yaml.safe_load(p.routed.new_path.read_text(encoding="utf-8").split("---", 2)[1])
    assert data["type"] == "skill"
    assert data["trigger"] == {"always": False, "keywords": ["replacement"], "file_patterns": []}
    assert result["patch"] == {"ok": True, "reason": "dynamic_trigger", "gene_changed": False}
    record = p.common.read_ledger()[-1]
    assert record["run_id"] == result["run_id"]
    assert len(record["files"]) == 2  # The publication pair is the whole new-operation write set.
    assert record["artifact_sha256"] == sha(p.routed.new_path)
    assert record["evidence_sha256"] == sha(p.routed.new_path.with_suffix(".evidence.json"))


@pytest.mark.parametrize("fault", ["missing_audit", "unbound", "review", "dimension", "source_identity",
    "draft_changed", "passed_draft_changed", "stage_changed", "sidecar_changed", "sidecar_missing", "source_changed",
    "draft_omitted", "artifact_omitted", "sidecar_omitted", "canonical_substitution"])
def test_guard_rejects_unapproved_or_changed_evidence_before_writing(publication, fault):
    p = publication
    audit = p.routed.audit
    sidecar = p.stage.with_suffix(".evidence.json")
    if fault == "missing_audit": p.routed.audit = {}
    elif fault == "unbound": p.routed.audit = {k: audit[k] for k in ("source_hash", "draft_path", "role", "verdict", "needs_human_review")}
    elif fault == "review": audit["needs_human_review"] = True
    elif fault == "dimension": audit["support_status"] = "UNKNOWN"
    elif fault == "source_identity": audit["source_hash"] = "other"
    elif fault == "draft_changed": p.path.write_text(p.path.read_text() + " ", encoding="utf-8")
    elif fault == "passed_draft_changed": p.draft["meta"]["name"] = "Changed"
    elif fault == "stage_changed": p.stage.write_text("changed", encoding="utf-8")
    elif fault == "sidecar_changed": sidecar.write_text(sidecar.read_text() + " ", encoding="utf-8")
    elif fault == "sidecar_missing": sidecar.unlink()
    elif fault == "source_changed": p.raw.write_text("changed", encoding="utf-8")
    elif fault == "draft_omitted": audit["draft_sha256"] = {}
    elif fault == "artifact_omitted": audit["artifact_paths"] = []
    elif fault == "sidecar_omitted": audit["audit_details"]["staging_sidecar_sha256"] = {}
    elif fault == "canonical_substitution":
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        payload["draft"]["meta"]["name"] = "Substituted"
        sidecar.write_text(json.dumps(payload), encoding="utf-8")
        audit["audit_details"]["staging_sidecar_sha256"][str(sidecar)] = sha(sidecar)
    result = p.mod.inject_one(p.routed, p.draft)
    assert not result["ok"], fault
    assert not p.routed.new_path.exists()
    assert not p.routed.new_path.with_suffix(".evidence.json").exists()
    assert not p.common.role_gene_file(p.routed.role).exists()
    assert not p.common.LEDGER_FILE.exists()


@pytest.mark.parametrize("fault", ["path", "role", "domain", "filename", "subdir"])
def test_path_escape_and_component_traversal_are_rejected(publication, tmp_path, fault):
    p = publication
    if fault == "path": p.routed.new_path = tmp_path / "outside.md"
    elif fault == "role": p.routed.role = "../escape"
    elif fault == "domain": p.routed.domain = "../escape"
    elif fault == "filename": p.routed.new_filename = "../outside.md"
    elif fault == "subdir": p.routed.target_subdir = p.common.VAULT_ROOT
    result = p.mod.inject_one(p.routed, p.draft)
    assert not result["ok"]
    assert not p.common.LEDGER_FILE.exists()


def test_dry_run_previews_missing_gene_without_any_vault_or_state_write(publication):
    p = publication
    before = set(p.common.VAULT_ROOT.rglob("*"))
    result = p.mod.inject_one(p.routed, p.draft, dry_run=True)
    assert result["ok"], result
    assert result["patch"]["reason"] == "dynamic_trigger"
    assert result["patch"]["gene_changed"] is False
    assert set(p.common.VAULT_ROOT.rglob("*")) == before
    assert not p.common.STATE_DIR.exists()


@pytest.mark.parametrize("existing", [False, True])
def test_existing_gene_is_untouched_and_missing_gene_is_never_created(publication, existing):
    p = publication
    gene = p.common.role_gene_file(p.routed.role)
    original = b"personal role gene without frontmatter\r\nnot generated content\r\n"
    if existing:
        gene.parent.mkdir(parents=True)
        gene.write_bytes(original)
    for dry_run in (True, False):
        result = p.mod.inject_one(p.routed, p.draft, dry_run=dry_run)
        assert result["ok"], result
        assert result["patch"]["gene_changed"] is False
        assert (gene.read_bytes() if gene.exists() else None) == (original if existing else None)


def test_compatibility_gene_patch_api_is_a_noop(publication):
    p = publication
    for dry_run in (True, False):
        result = p.mod.patch_role_gene(p.routed.role, p.routed.new_filename, dry_run=dry_run)
        assert result["ok"] and result["gene_changed"] is False
        assert not p.common.role_gene_file(p.routed.role).exists()


@pytest.mark.parametrize("foreign", ["markdown", "sidecar", "edited_markdown", "edited_sidecar"])
def test_foreign_or_edited_pair_is_never_overwritten(publication, foreign):
    p = publication
    target = p.routed.new_path if "markdown" in foreign else p.routed.new_path.with_suffix(".evidence.json")
    if foreign.startswith("edited"):
        assert p.mod.inject_one(p.routed, p.draft)["ok"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("my manual content", encoding="utf-8")
    before = target.read_bytes()
    assert not p.mod.inject_one(p.routed, p.draft)["ok"]
    assert target.read_bytes() == before


def test_rollback_restores_pair_and_existing_gene_exactly(publication):
    p = publication
    gene = p.common.role_gene_file(p.routed.role)
    gene.parent.mkdir(parents=True)
    original = b'---\nrole: custom\nskill_refs: []\nrule_refs: []\n---\n\n# Personal notes\n'
    gene.write_bytes(original)
    result = p.mod.inject_one(p.routed, p.draft)
    assert result["ok"], result
    rolled = p.rollback.rollback_run(result["run_id"])
    assert rolled["ok"], rolled
    assert gene.read_bytes() == original
    assert not p.routed.new_path.exists()
    assert not p.routed.new_path.with_suffix(".evidence.json").exists()


@pytest.mark.parametrize("edit", ["markdown", "sidecar"])
def test_manual_rollback_protects_all_files_when_any_member_was_edited(publication, edit):
    p = publication
    result = p.mod.inject_one(p.routed, p.draft)
    assert result["ok"]
    files = [p.routed.new_path, p.routed.new_path.with_suffix(".evidence.json")]
    target = files[["markdown", "sidecar"].index(edit)]
    target.write_bytes(target.read_bytes() + b"manual edit")
    before = [path.read_bytes() for path in files]
    assert not p.rollback.rollback_run(result["run_id"])["ok"]
    assert [path.read_bytes() for path in files] == before


def test_operation_ids_do_not_group_unrelated_same_second_injections(publication):
    p = publication
    first = p.mod.inject_one(p.routed, p.draft)
    second = p.mod.inject_one(p.routed, p.draft)
    assert first["ok"] and second["ok"]
    assert first["run_id"] != second["run_id"]
    assert p.rollback.rollback_run(second["run_id"])["ok"]
    assert p.routed.new_path.exists()
    assert p.rollback.rollback_run(first["run_id"])["ok"]


def test_rollback_rejects_outside_ledger_path(publication, tmp_path):
    p = publication
    outside = tmp_path / "outside.md"
    outside.write_text("retain", encoding="utf-8")
    p.common.append_ledger({"action": "skill_file_copied", "relpath": str(outside), "ts": "historical-run"})
    result = p.rollback.rollback_run("historical-run")
    assert not result["ok"]
    assert outside.read_text() == "retain"


@pytest.mark.parametrize("fault", ["sidecar_write", "artifact_corrupted_during_write", "sidecar_corrupted_during_write", "ledger_write"])
def test_partial_io_failure_restores_existing_owned_pair(publication, monkeypatch, fault):
    p = publication
    assert p.mod.inject_one(p.routed, p.draft)["ok"]
    files = [p.routed.new_path, p.routed.new_path.with_suffix(".evidence.json")]
    before = [path.read_bytes() for path in files]
    original = p.mod.atomic_write_text
    def write(path, text):
        if fault == "sidecar_write" and path == files[1]:
            raise OSError("simulated sidecar write failure")
        original(path, text)
        if fault == "artifact_corrupted_during_write" and path == files[0]:
            path.write_text("wrong canonical body", encoding="utf-8")
        if fault == "sidecar_corrupted_during_write" and path == files[1]:
            path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(p.mod, "atomic_write_text", write)
    if fault == "ledger_write":
        def fail(record):
            raise OSError("simulated ledger failure")
        monkeypatch.setattr(p.mod, "append_ledger", fail)
    result = p.mod.inject_one(p.routed, p.draft)
    assert not result["ok"]
    assert [path.read_bytes() for path in files] == before


def test_legacy_non_skill_uses_bound_audit_without_semantic_dimensions(publication):
    p = publication
    draft = p.draft
    draft.pop("schema_version")
    draft.pop("distillation")
    draft["source"]["doc_type"] = "blog"
    draft["key_concepts"] = [{"title": "Legacy principle", "explanation": "Legacy knowledge remains usable."}]
    p.path.write_text(json.dumps(draft), encoding="utf-8")
    p.stage.unlink()
    p.stage.with_suffix(".evidence.json").unlink()
    stage = p.renderer.publish_to_vault(draft, {"vault_dir": str(p.stage.parent)}, output_dir=p.stage.parent)
    p.routed.audit.update(schema_version="", draft_sha256={str(p.path): sha(p.path)},
                          artifact_paths=[str(stage)], artifact_sha256={str(stage): sha(stage)})
    p.routed.audit["audit_details"]["staging_sidecar_sha256"] = {str(stage.with_suffix(".evidence.json")): sha(stage.with_suffix(".evidence.json"))}
    for dimension in ("coverage_status", "support_status", "synthesis_status", "source_inventory_status"):
        p.routed.audit[dimension] = "UNKNOWN"
    result = p.mod.inject_one(p.routed, draft)
    assert result["ok"], result
    text = p.routed.new_path.read_text(encoding="utf-8")
    assert "Legacy knowledge remains usable." in text
    assert "## 1. 核心约束" in text
    assert result["patch"]["gene_changed"] is False
    assert not p.common.role_gene_file(p.routed.role).exists()
    data = yaml.safe_load(text.split("---", 2)[1])
    assert data["type"] == "skill"
    assert data["trigger"]["keywords"] == ["replacement"]
    p.renderer.load_artifact_evidence(p.routed.new_path)


def add_second_card(p):
    second = copy.deepcopy(p.draft)
    second["uuid"] = "second-card"
    second["meta"]["name"] = "Second recovery card"
    second["meta"]["trigger_keywords"] = ["original"]
    path = p.path.with_name("second.json")
    path.write_text(json.dumps(second), encoding="utf-8")
    stage = p.renderer.publish_to_vault(second, {"vault_dir": str(p.stage.parent)}, output_dir=p.stage.parent)
    audit = p.routed.audit
    audit["draft_paths"].append(str(path))
    audit["draft_sha256"][str(path)] = sha(path)
    audit["artifact_paths"].append(str(stage))
    audit["artifact_sha256"][str(stage)] = sha(stage)
    audit["audit_details"]["staging_sidecar_sha256"][str(stage.with_suffix(".evidence.json"))] = sha(stage.with_suffix(".evidence.json"))
    routed = replace(p.routed, draft_path=str(path), next_n=2, new_filename="TL2-second.md",
                     new_path=p.routed.target_subdir / "TL2-second.md", audit=copy.deepcopy(audit))
    routed.audit["draft_path"] = str(path)
    return path, stage, routed


def test_changed_sibling_card_blocks_publication_of_unchanged_current_card(publication):
    p = publication
    path, _, _ = add_second_card(p)
    path.write_text(path.read_text() + " ", encoding="utf-8")
    result = p.mod.inject_one(p.routed, p.draft)
    assert not result["ok"]
    assert not p.routed.new_path.exists()


def test_standalone_cli_reads_configured_state_and_injects_all_routed_cards(publication):
    p = publication
    _, _, second = add_second_card(p)
    records = []
    for routed in (p.routed, second):
        record = asdict(routed)
        for key in ("new_path", "target_subdir"):
            record[key] = str(record[key])
        records.append(json.dumps(record, ensure_ascii=False))
    p.common.STATE_DIR.mkdir()
    (p.common.STATE_DIR / "routed.jsonl").write_text("\n".join(records) + "\n", encoding="utf-8")
    assert p.mod.main(["--dry-run"]) == 0
    assert not p.routed.new_path.exists()
    assert p.mod.main([]) == 0
    for routed in (p.routed, second):
        p.renderer.load_artifact_evidence(routed.new_path)
    assert p.common.RETROSPECTIVE_DIR.exists()


def test_standalone_cli_reports_malformed_card_as_failure(publication):
    p = publication
    p.common.STATE_DIR.mkdir()
    (p.common.STATE_DIR / "routed.jsonl").write_text("{broken\n", encoding="utf-8")
    assert p.mod.main([]) == 1
    assert not p.routed.new_path.exists()


def test_hash_bound_legacy_rollback_is_still_supported(publication):
    p = publication
    path = p.routed.new_path
    path.parent.mkdir(parents=True)
    path.write_text("generated legacy knowledge", encoding="utf-8")
    p.common.append_ledger({"action": "skill_file_copied", "relpath": path.relative_to(p.common.VAULT_ROOT).as_posix(),
                            "artifact_sha256": sha(path), "ts": "legacy-bound-run"})
    result = p.rollback.rollback_run("legacy-bound-run")
    assert result["ok"], result
    assert not path.exists()


@pytest.mark.parametrize("keywords", [[], [""], ["  "], ["replacement", 42], "replacement", None])
def test_semantic_keywords_require_audited_nonempty_string_list(publication, keywords):
    p = publication
    p.draft["meta"]["trigger_keywords"] = keywords
    p.draft["learning_enhancement"]["knowledge_tags"] = ["fallback-is-not-a-semantic-trigger"]
    with pytest.raises(ValueError, match="trigger"):
        p.mod.render_vault_skill(p.routed, p.draft)


def test_fully_colliding_keywords_require_review_before_writes(publication):
    p = publication
    other = p.routed.target_subdir / "TL0-other.md"
    other.parent.mkdir(parents=True)
    other.write_text('---\ntype: skill\ntrigger:\n  keywords: [Replacement]\n---\n# Existing task\n', encoding="utf-8")
    result = p.mod.inject_one(p.routed, p.draft)
    assert not result["ok"]
    assert result["stage"] == "trigger"
    assert result["needs_human_review"] is True
    assert "trigger_keywords_not_unique" in result["error"]
    assert not p.routed.new_path.exists()
    assert not p.common.LEDGER_FILE.exists()
    assert not p.common.role_gene_file(p.routed.role).exists()


def test_one_directory_unique_keyword_suffices_without_inventing_role_fallback(publication):
    p = publication
    other = p.routed.target_subdir / "TL0-other.md"
    other.parent.mkdir(parents=True)
    other.write_text('---\ntrigger:\n  keywords: ["unrelated task"]\n---\n', encoding="utf-8")
    result = p.mod.inject_one(p.routed, p.draft)
    assert result["ok"], result
    data = yaml.safe_load(p.routed.new_path.read_text(encoding="utf-8").split("---", 2)[1])
    assert data["trigger"]["keywords"] == p.draft["meta"]["trigger_keywords"]


def test_rollback_does_not_touch_gene_edited_after_pair_publication(publication):
    p = publication
    gene = p.common.role_gene_file(p.routed.role)
    gene.parent.mkdir(parents=True)
    gene.write_text("Original personal gene", encoding="utf-8")
    result = p.mod.inject_one(p.routed, p.draft)
    assert result["ok"]
    gene.write_bytes(b"User edited gene after publication")
    rolled = p.rollback.rollback_run(result["run_id"])
    assert rolled["ok"], rolled
    assert gene.read_bytes() == b"User edited gene after publication"


def test_hash_bound_historical_gene_rollback_remains_supported(publication):
    p = publication
    gene = p.common.role_gene_file(p.routed.role)
    gene.parent.mkdir(parents=True)
    prior = b"Historical original gene"
    gene.with_suffix(".md.bak").write_bytes(prior)
    gene.write_bytes(b"Historical generated gene")
    p.common.append_ledger({"action": "frontmatter_patched", "relpath": gene.relative_to(p.common.VAULT_ROOT).as_posix(),
        "before_sha256": hashlib.sha256(prior).hexdigest(), "after_sha256": sha(gene), "ts": "historical-gene"})
    result = p.rollback.rollback_run("historical-gene")
    assert result["ok"], result
    assert gene.read_bytes() == prior


@pytest.mark.parametrize("filename,content", [
    ("_index.md", "Private index without frontmatter\n"),
    (".helper.md", '---\ntrigger:\n  keywords: [replacement]\n---\nInternal helper\n'),
])
def test_trigger_inventory_ignores_engine_excluded_helper_files(publication, filename, content):
    p = publication
    helper = p.routed.target_subdir / filename
    helper.parent.mkdir(parents=True)
    helper.write_text(content, encoding="utf-8")
    original = helper.read_bytes()
    result = p.mod.inject_one(p.routed, p.draft)
    assert result["ok"], result
    p.renderer.load_artifact_evidence(p.routed.new_path)
    assert helper.read_bytes() == original


def rebind_current_metadata(p, keywords):
    """Persist/re-stage the changed card and bind its new approved snapshot."""
    p.draft["meta"]["trigger_keywords"] = keywords
    p.path.write_text(json.dumps(p.draft), encoding="utf-8")
    p.stage = p.renderer.publish_to_vault(p.draft, {"vault_dir": str(p.stage.parent)}, output_dir=p.stage.parent)
    p.routed.audit["draft_sha256"] = {str(p.path): sha(p.path)}
    p.routed.audit["artifact_paths"] = [str(p.stage)]
    p.routed.audit["artifact_sha256"] = {str(p.stage): sha(p.stage)}
    sidecar = p.stage.with_suffix(".evidence.json")
    p.routed.audit["audit_details"]["staging_sidecar_sha256"] = {str(sidecar): sha(sidecar)}


@pytest.mark.parametrize("update", [False, True])
def test_incoming_card_cannot_remove_an_existing_cards_last_unique_keyword(publication, update):
    p = publication
    if update:
        rebind_current_metadata(p, ["old independent task"])
        assert p.mod.inject_one(p.routed, p.draft)["ok"]
    other = p.routed.target_subdir / "TL0-other.md"
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text('---\ntrigger:\n  keywords: [replacement]\n---\nExisting skill\n', encoding="utf-8")
    rebind_current_metadata(p, ["replacement", "new task"])
    files = [p.routed.new_path, p.routed.new_path.with_suffix(".evidence.json"), other]
    before = [path.read_bytes() if path.exists() else None for path in files]
    result = p.mod.inject_one(p.routed, p.draft)
    assert not result["ok"]
    assert result["stage"] == "trigger"
    assert result["needs_human_review"] is True
    assert "trigger_existing_keyword_uniqueness_lost" in result["error"]
    assert [path.read_bytes() if path.exists() else None for path in files] == before


def test_keyword_overlap_is_allowed_when_existing_card_retains_another_unique_term(publication):
    p = publication
    other = p.routed.target_subdir / "TL0-other.md"
    other.parent.mkdir(parents=True)
    other.write_text('---\ntrigger:\n  keywords: [replacement, "retained independent task"]\n---\n', encoding="utf-8")
    rebind_current_metadata(p, ["replacement", "new task"])
    result = p.mod.inject_one(p.routed, p.draft)
    assert result["ok"], result
    actual = yaml.safe_load(p.routed.new_path.read_text(encoding="utf-8").split("---", 2)[1])
    assert actual["trigger"]["keywords"] == ["replacement", "new task"]


def test_new_card_does_not_demand_repair_of_preexisting_keyword_collisions_or_nonkeyword_triggers(publication):
    p = publication
    p.routed.target_subdir.mkdir(parents=True)
    for name, trigger in (
        ("TL0-one.md", {"keywords": ["replacement"]}),
        ("TL0-two.md", {"keywords": ["replacement"]}),
        ("TL0-always.md", {"always": True}),
        ("TL0-pattern.md", {"file_patterns": ["*.py"]}),
    ):
        (p.routed.target_subdir / name).write_text("---\n" + yaml.safe_dump({"trigger": trigger}) + "---\n", encoding="utf-8")
    rebind_current_metadata(p, ["replacement", "new task"])
    result = p.mod.inject_one(p.routed, p.draft)
    assert result["ok"], result


def test_update_accounts_for_old_self_when_detecting_preexisting_bad_pool(publication):
    p = publication
    assert p.mod.inject_one(p.routed, p.draft)["ok"]
    (p.routed.target_subdir / "TL0-old-collision.md").write_text('---\ntrigger:\n  keywords: [replacement]\n---\n', encoding="utf-8")
    rebind_current_metadata(p, ["replacement", "new task"])
    result = p.mod.inject_one(p.routed, p.draft)
    assert result["ok"], result


@pytest.mark.parametrize("prior_digest", [None, "mismatch", "malformed"])
def test_historical_gene_rollback_refuses_unbound_or_overwritten_backup(publication, prior_digest):
    p = publication
    gene = p.common.role_gene_file(p.routed.role)
    gene.parent.mkdir(parents=True)
    gene.write_bytes(b"Historical generated gene")
    backup = gene.with_suffix(".md.bak")
    backup.write_bytes(b"Overwritten arbitrary backup bytes")
    record = {"action": "frontmatter_patched", "relpath": gene.relative_to(p.common.VAULT_ROOT).as_posix(),
              "after_sha256": sha(gene), "ts": "unbound-gene"}
    if prior_digest == "mismatch":
        record["before_sha256"] = hashlib.sha256(b"Expected prior gene").hexdigest()
    elif prior_digest == "malformed":
        record["before_sha256"] = "invalid digest"
    p.common.append_ledger(record)
    before = gene.read_bytes(), backup.read_bytes()
    result = p.rollback.rollback_run("unbound-gene")
    assert not result["ok"]
    assert (gene.read_bytes(), backup.read_bytes()) == before
