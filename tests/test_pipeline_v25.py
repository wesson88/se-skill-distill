import json
from pathlib import Path
from types import SimpleNamespace
from importlib.machinery import SourceFileLoader
from conftest import load_script


def pipeline_fixture(isolated, tmp_path, monkeypatch, *, verdict="PASS", cards=1, ingest_empty=False, inject_error=None):
    pipeline = load_script("pipeline.py")
    cfg = tmp_path / "sources.yaml"
    source = {"kind":"local_dir", "path":"unused", "target_role":"技术主管"}
    cfg.write_text(json.dumps({"sources":[source]}), encoding="utf-8")
    info = {"source_hash":"hash", "raw_path":"raw.md", "doc_type":"blog"}
    paths = []
    for index in range(cards):
        path = tmp_path / f"draft{index}.json"
        path.write_text(json.dumps({"uuid":f"draft-{index}", "meta":{"name":f"流程{index}"},
                                   "source":{"source_hash":"hash", "doc_type":"blog"}}), encoding="utf-8")
        paths.append(str(path))
    report = isolated.AuditResult("hash", paths[0], "技术主管", coverage=None if verdict=="UNKNOWN" else 100,
                                  verdict=verdict, needs_human_review=verdict!="PASS",
                                  draft_paths=paths)
    calls = {"published":[], "written":[], "audit_results":[]}
    def publish(source_infos=None, console=None, **kwargs):
        calls["published"].append((source_infos, kwargs))
        return cards
    def inject(routed, draft, dry_run=False):
        calls["written"].append(routed)
        if inject_error:
            return {"ok":False,"stage":"trigger","error":inject_error,"needs_human_review":True}
        if not dry_run:
            routed.new_path.parent.mkdir(parents=True,exist_ok=True)
            routed.new_path.write_text("body",encoding="utf-8")
        return {"ok":True,"patch":{"ok":True}}
    modules = {
        "01_ingest.py":SimpleNamespace(ingest_batch=lambda sources,console=None:[(source,[] if ingest_empty else [info])]),
        "02_audit.py":SimpleNamespace(
            publish_all_drafts=publish,
            audit_one=lambda *a,**kw:report,
            write_audit_results=lambda results:calls["audit_results"].extend(results),
            extract_batch=lambda *a,**kw:[{"source":info,"drafts":[]}]),
        "03_route.py":load_script("03_route.py"),
        "04_inject.py":SimpleNamespace(inject_one=inject, write_retrospective_log=lambda *a:None),
    }
    monkeypatch.setattr(SourceFileLoader,"load_module",lambda self,*a:modules[Path(self.path).name])
    monkeypatch.setattr(pipeline,"_load_stage",lambda filename:modules[filename],raising=False)
    return pipeline,cfg,calls


def test_unknown_audit_returns_failure_and_review_record(isolated,tmp_path,monkeypatch):
    pipeline,cfg,calls=pipeline_fixture(isolated,tmp_path,monkeypatch,verdict="UNKNOWN")
    assert pipeline.run_pipeline(cfg,skip_extract=True)==1
    assert calls["audit_results"][0].verdict=="UNKNOWN"
    assert not calls["written"]


def test_semantic_failure_is_not_success_exit(isolated,tmp_path,monkeypatch):
    pipeline,cfg,calls=pipeline_fixture(isolated,tmp_path,monkeypatch,verdict="FAIL")
    assert pipeline.run_pipeline(cfg,skip_extract=True)==1
    assert not calls["written"]


def test_all_cards_are_injected_with_unique_paths_and_scoped_publish(isolated,tmp_path,monkeypatch):
    pipeline,cfg,calls=pipeline_fixture(isolated,tmp_path,monkeypatch,cards=2)
    assert pipeline.run_pipeline(cfg,skip_extract=True)==0
    assert len(calls["written"])==2
    assert len({str(rs.new_path) for rs in calls["written"]})==2
    assert len({rs.next_n for rs in calls["written"]})==2
    assert calls["published"][0][0][0]["source_hash"]=="hash"
    assert calls["published"][0][1]["output_dir"].is_relative_to(isolated.STATE_DIR)
    assert all(rs.audit["verdict"]=="PASS" for rs in calls["written"])


def test_dry_run_routes_all_cards_without_writing_vault(isolated,tmp_path,monkeypatch):
    pipeline,cfg,calls=pipeline_fixture(isolated,tmp_path,monkeypatch,cards=2)
    assert pipeline.run_pipeline(cfg,skip_extract=True,dry_run=True)==0
    assert len(calls["written"])==2
    assert len({str(rs.new_path) for rs in calls["written"]})==2
    assert len({rs.next_n for rs in calls["written"]})==2
    assert not list(isolated.VAULT_ROOT.rglob("*.md"))


def test_empty_ingest_is_failed_not_silent_success(isolated,tmp_path,monkeypatch):
    pipeline,cfg,calls=pipeline_fixture(isolated,tmp_path,monkeypatch,ingest_empty=True)
    assert pipeline.run_pipeline(cfg,skip_extract=True)==1
    assert calls["audit_results"][0].verdict=="UNKNOWN"


def test_trigger_failure_reason_is_preserved_for_review(isolated,tmp_path,monkeypatch):
    reason="trigger_keywords_not_unique: no audited keyword is unique"
    pipeline,cfg,calls=pipeline_fixture(isolated,tmp_path,monkeypatch,inject_error=reason)
    assert pipeline.run_pipeline(cfg,skip_extract=True)==1
    report=calls["audit_results"][0]
    assert reason in "\n".join(report.notes)
    assert report.audit_details["injection_errors"][0]["stage"]=="trigger"
