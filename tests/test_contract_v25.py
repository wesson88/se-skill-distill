import pytest
from conftest import load_script


def test_unknown_defaults_do_not_authorize_injection(isolated):
    result = isolated.AuditResult("hash", "draft.json", "技术主管")
    assert result.verdict == "UNKNOWN"
    assert result.coverage is None
    assert result.needs_human_review is True
    assert result.is_approved is False


def test_semantic_pass_requires_all_dimensions(isolated):
    result = isolated.AuditResult("hash", "draft.json", "技术主管",
                                  verdict="PASS", needs_human_review=False)
    result.schema_version = "semantic_distillation_v1"
    assert result.is_approved is False
    for field in ("coverage_status", "support_status", "synthesis_status", "source_inventory_status"):
        setattr(result, field, "PASS")
    assert result.is_approved is True
    result.support_status = "FAIL"
    assert result.is_approved is False


@pytest.mark.parametrize("url", [
    "https://github.com/owner/repo/blob/pinned/skills/tool/SKILL.md",
    "https://raw.githubusercontent.com/owner/repo/pinned/tool.skill.md",
])
def test_single_skill_url_uses_skill_collector(url):
    entry = load_script("distill_url.py").resolve_url(url)[0]
    assert entry["kind"] == "github_raw"
    assert "/pinned/" in entry["url"]


def test_readme_keeps_document_type():
    entry = load_script("distill_url.py").resolve_url(
        "https://github.com/owner/repo/blob/main/README.md")[0]
    assert entry["kind"] == "single_url"


@pytest.mark.parametrize("url", [
    "https://github.com/owner/repo/blob/pinned/skills/tool/SKILL.md",
    "https://raw.githubusercontent.com/owner/repo/pinned/skills/tool/SKILL.md",
])
def test_single_skill_download_uses_real_collector_http_boundary(url, isolated, tmp_path, monkeypatch):
    import httpx
    from skillmind import collector
    collector_body = "# 定位问题\n\n先定位根因，再修复。\n"
    expected = "https://raw.githubusercontent.com/owner/repo/pinned/skills/tool/SKILL.md"
    def download(target, **kwargs):
        assert target == expected
        return httpx.Response(200, text=collector_body, request=httpx.Request("GET",target))
    monkeypatch.setattr(httpx, "get", download)
    monkeypatch.setattr(collector, "_load_hashes", lambda:{})
    monkeypatch.setattr(collector, "_save_hashes", lambda hashes:None)
    monkeypatch.setattr(collector, "RAW_DIR", tmp_path/"raw")
    def cannot_clone(*args, **kwargs):
        pytest.fail("Single Skill input must download, never clone a raw URL")
    monkeypatch.setattr(collector, "clone_or_pull", cannot_clone)
    entry = load_script("distill_url.py").resolve_url(url)[0]
    document = load_script("01_ingest.py").ingest_one(entry)[0]
    assert document["doc_type"] == "skill"
    from pathlib import Path
    assert Path(document["raw_path"]).read_text(encoding="utf-8") == collector_body


def test_single_url_config_skill_still_uses_skill_collector(isolated, monkeypatch):
    ingest = load_script("01_ingest.py")
    from types import SimpleNamespace
    calls = []
    collector = SimpleNamespace(
        ingest_skill=lambda *a, **kw: calls.append("skill") or [{"doc_type": "skill"}],
        ingest_url=lambda *a, **kw: calls.append("blog") or [{"doc_type": "blog"}],
    )
    monkeypatch.setattr(ingest, "import_skillmind", lambda: (collector, None, None))
    docs = ingest.ingest_one({"kind": "single_url", "url": "https://raw.githubusercontent.com/o/r/main/SKILL.md"})
    assert calls == ["skill"]
    assert docs[0]["doc_type"] == "skill"


def test_role_detection_reads_semantic_body(isolated):
    route = load_script("03_route.py")
    draft = {"meta": {"name": "实践", "intent": "改进工作"},
             "distillation": {"sections": [{"title": "界面", "items": [{"text": "使用 CSS 调整 DOM 渲染"}]}]}}
    assert route.resolve_role("auto", draft, {}) == "前端工程师"


def test_route_carries_audit_and_rejects_unknown(isolated):
    route = load_script("03_route.py")
    draft = {"meta": {"name": "流程"}}
    report = isolated.AuditResult("hash", "draft.json", "技术主管")
    assert route.route_one(report, draft, {}, "技术主管") is None
    report.verdict = "PASS"
    report.needs_human_review = False
    routed = route.route_one(report, draft, {}, "技术主管")
    assert routed.audit["source_hash"] == "hash"


def test_route_reuses_owned_uuid_instead_of_duplicate_skill_number(isolated):
    import hashlib,json
    route=load_script("03_route.py")
    directory=isolated.skill_subdir("技术主管")
    directory.mkdir(parents=True,exist_ok=True)
    existing=directory/"TL1-原有技能.md"
    existing.write_text("受管旧正文",encoding="utf-8")
    existing.with_suffix(".evidence.json").write_text(json.dumps({
        "artifact_sha256":hashlib.sha256(existing.read_bytes()).hexdigest(),
        "draft":{"uuid":"stable-uuid"}}),encoding="utf-8")
    draft={"uuid":"stable-uuid","meta":{"name":"更新标题"}}
    result=isolated.AuditResult("hash","draft.json","技术主管",verdict="PASS",needs_human_review=False)
    routed=route.route_one(result,draft,{},"技术主管")
    assert routed.new_path==existing
    assert routed.next_n==1
