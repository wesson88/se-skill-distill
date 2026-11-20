"""
阶段 5: Inject — 写入由目录和 trigger 动态召回的 vault skill

审核绑定草稿、暂存正文及证据后，仅写入最终 Markdown/证据对。角色基因不参与登记。
ledger 保存独立操作 ID、写后哈希和原始字节；部分失败恢复原状。
"""
from __future__ import annotations

import json
import re
import sys
import time
from uuid import uuid4
from datetime import date
from pathlib import Path

import _common
from semantic_publication import (
    SEMANTIC_SCHEMA, assert_owned_pair, canonical_json, complete_snapshots,
    evidence_draft, renderer_module, restore_snapshots, sha256_file,
    snapshot, validate_audit, validate_destinations, validate_semantic, vault_path,
)
from _common import (
    RoutedSkill,
    append_ledger,
    atomic_write_text,
)


# vault 路径在 set_vault_root() 后会改,这里用函数动态查
def _retrospective_dir() -> Path:
    return _common.RETROSPECTIVE_DIR


def _vault_root() -> Path:
    return _common.VAULT_ROOT


# ---------------------------------------------------------------------------
# vault skill 文件渲染
# ---------------------------------------------------------------------------

def render_vault_skill(routed: RoutedSkill, draft: dict) -> str:
    """
    把 skillMind 草稿渲染成 vault 现有格式的 skill 文件。
    合并 skillMind 默认 frontmatter(uuid / source_reliability / obsolescence_risk / doc_type)
    与 vault 现有字段(role / skill_id / type / lifecycle)。
    """
    validate_semantic(draft)
    if draft.get("schema_version") == SEMANTIC_SCHEMA:
        return _render_semantic_skill(routed, draft)
    meta = draft.get("meta", {})
    source = draft.get("source", {})
    le = draft.get("learning_enhancement", {})

    role_label_map = {
        "后端工程师": "后端工程师",
        "前端工程师": "前端工程师",
        "架构师": "架构师",
        "技术主管": "技术主管",
        "产品经理": "产品经理",
    }
    role = role_label_map.get(routed.role, routed.role)
    skill_id = f"{routed.prefix}{routed.next_n}"
    # 渲染 markdown 正文
    body_lines: list[str] = []
    body_lines.append(f"# {meta.get('name', '未命名')}")
    body_lines.append("")

    # 一句话总结
    if le.get("plain_summary"):
        body_lines.append(f"> {le['plain_summary']}")
        body_lines.append("")

    # 触发关键词
    if meta.get("trigger_keywords"):
        kw = meta["trigger_keywords"]
        if isinstance(kw, list):
            kw = "、".join(kw)
        body_lines.append(f"**触发关键词**: {kw}")
        body_lines.append("")

    # 1. 核心约束
    body_lines.append("## 1. 核心约束")
    body_lines.append("")
    intent = meta.get("intent") or meta.get("source_description") or "待人工补充"
    body_lines.append(f"- **目的**: {intent}")
    preconditions = draft.get("preconditions", [])
    if preconditions:
        body_lines.append("- **前置条件**:")
        for pc in preconditions:
            body_lines.append(f"  - {pc}")
    halt = draft.get("halt_conditions", [])
    if halt:
        body_lines.append("- **中止条件**:")
        for h in halt:
            body_lines.append(f"  - {h}")
    body_lines.append("")

    # 2. 关键概念
    body_lines.append("## 2. 关键概念(原文提炼)")
    body_lines.append("")
    kc_list = draft.get("key_concepts", [])
    for i, kc in enumerate(kc_list, 1):
        body_lines.append(f"### {i}. {kc.get('title', '?')}")
        body_lines.append("")
        body_lines.append(kc.get("explanation", ""))
        if kc.get("example"):
            body_lines.append("")
            body_lines.append("**示例**:")
            body_lines.append("")
            body_lines.append("```")
            body_lines.append(kc["example"])
            body_lines.append("```")
        body_lines.append("")

    # 3. 执行流程
    proc = draft.get("procedure", [])
    if proc:
        body_lines.append("## 3. 执行流程")
        body_lines.append("")
        for p in proc:
            if isinstance(p, dict):
                seq = p.get("seq", "?")
                action = p.get("action", "")
                cmd = p.get("command", "")
                body_lines.append(f"{seq}. {action}")
                if cmd:
                    body_lines.append(f"   ```")
                    body_lines.append(f"   {cmd}")
                    body_lines.append(f"   ```")
            else:
                body_lines.append(f"- {p}")
        body_lines.append("")

    # 4. 决策点
    dp_list = draft.get("decision_points", [])
    if dp_list:
        body_lines.append("## 4. 决策点")
        body_lines.append("")
        for dp in dp_list:
            if isinstance(dp, dict):
                body_lines.append(f"- **if** {dp.get('condition', '?')}: {dp.get('then', '?')}")
                if dp.get("else"):
                    body_lines.append(f"  **else**: {dp['else']}")
            else:
                body_lines.append(f"- {dp}")
        body_lines.append("")

    # 5. 避坑
    pain = le.get("pain_points", [])
    if pain:
        body_lines.append("## 5. 避坑")
        body_lines.append("")
        if isinstance(pain, list):
            for p in pain:
                body_lines.append(f"- {p}")
        else:
            body_lines.append(str(pain))
        body_lines.append("")

    # 6. 反向引用
    cr = draft.get("cross_references", [])
    if cr:
        body_lines.append("## 6. 关联引用")
        body_lines.append("")
        for c in cr:
            body_lines.append(f"- {c}")
        body_lines.append("")

    # 7. 溯源(自动化追加)
    body_lines.append("## 7. 溯源")
    body_lines.append("")
    body_lines.append(f"- **来源**: {source.get('source_url') or source.get('repo_url', '?')}")
    body_lines.append(f"- **可信度**: {draft.get('source_reliability', 'medium')}")
    body_lines.append(f"- **过时风险**: {draft.get('obsolescence_risk', 'medium')}")
    body_lines.append(f"- **蒸馏时间**: {time.strftime('%Y-%m-%dT%H:%M:%S')}")
    body_lines.append("")

    body = "\n".join(body_lines)

    # frontmatter
    # trigger:让 agent-workflow 能按关键词决定是否注入(缺 trigger 会被 fail-closed 跳过)
    trig_kws = _trigger(draft)["keywords"]

    fm_lines: list[str] = ["---"]
    fm_lines.append(f"role: {role}")
    fm_lines.append(f"skill_id: {skill_id}")
    fm_lines.append("type: skill")
    fm_lines.append(f"lifecycle: NEW")
    fm_lines.append("trigger:")
    fm_lines.append("  always: false")
    fm_lines.append("  keywords:")
    for kw in trig_kws:
        fm_lines.append(f"    - {json.dumps(kw, ensure_ascii=False)}")
    fm_lines.append("  file_patterns: []")
    fm_lines.append(f"uuid: {draft.get('uuid', '')}")
    fm_lines.append(f"source_url: {json.dumps(source.get('source_url') or source.get('repo_url', ''), ensure_ascii=False)}")
    fm_lines.append(f"source_hash: {source.get('source_hash', '')}")
    fm_lines.append(f"source_reliability: {draft.get('source_reliability', 'medium')}")
    fm_lines.append(f"obsolescence_risk: {draft.get('obsolescence_risk', 'medium')}")
    fm_lines.append(f"doc_type: {source.get('doc_type', 'blog')}")
    fm_lines.append("---")
    fm_lines.append("")
    fm = "\n".join(fm_lines)

    return fm + "\n" + body


# ---------------------------------------------------------------------------
# 动态 trigger frontmatter 及兼容接口
# ---------------------------------------------------------------------------

def _render_semantic_skill(routed: RoutedSkill, draft: dict) -> str:
    """Keep SkillMind's exact canonical body; only extend its frontmatter."""
    import yaml
    trigger = _trigger(draft)
    renderer = renderer_module()
    # v2.5 renderer has no content settings; a nonempty cfg avoids loading a
    # machine's default config just to format an already approved document.
    rendered = renderer.render_to_markdown(evidence_draft(draft), {"se_publication": True})
    _, frontmatter, body = rendered.split("---", 2)
    data = yaml.safe_load(frontmatter)
    data.update(role=routed.role, skill_id=f"{routed.prefix}{routed.next_n}", lifecycle="NEW",
                schema_version=SEMANTIC_SCHEMA, type="skill", trigger=trigger)
    for key in ("source_url", "source_repo", "parent_source"):
        if key in data:
            data[key] = renderer._QuotedLink(data[key])
    encoded = yaml.dump(data, Dumper=renderer._FrontmatterDumper, allow_unicode=True,
                        default_flow_style=False, sort_keys=False).strip()
    return f"---\n{encoded}\n---{body}"


class TriggerPolicyError(ValueError):
    """Source metadata needs review before the skill can enter dynamic recall."""


def _trigger(draft: dict) -> dict:
    meta = draft.get("meta") or {}
    keywords = meta.get("trigger_keywords")
    if draft.get("schema_version") != SEMANTIC_SCHEMA and keywords in (None, []):
        keywords = (draft.get("learning_enhancement") or {}).get("knowledge_tags")
    if (not isinstance(keywords, list) or not keywords
            or not all(isinstance(value, str) and value.strip() for value in keywords)):
        raise TriggerPolicyError("trigger_keywords_invalid: audited keywords must be a nonempty string list")
    # Only audited metadata supplies terms. Never invent role/category fallbacks.
    cleaned, seen = [], set()
    for value in keywords:
        keyword = value.strip()
        if keyword.casefold() not in seen:
            seen.add(keyword.casefold())
            cleaned.append(keyword)
    return {"always": False, "keywords": cleaned, "file_patterns": []}


def _validate_trigger_pool(routed: RoutedSkill, draft: dict) -> None:
    """Enforce vault role-gene norm section 10.4's directory-unique keyword."""
    from collections import Counter
    import yaml
    keywords = {value.casefold() for value in _trigger(draft)["keywords"]}
    current = Path(routed.new_path).resolve()
    previous = {}
    for path in sorted(Path(routed.target_subdir).glob("*.md")):
        # Vault norm section 10.2 / engine _skill_files exclude helper notes.
        if path.name.startswith(("_", ".")):
            continue
        try:
            text = path.read_text(encoding="utf-8")
            parts = re.split(r"^---[ \t]*$", text, maxsplit=2, flags=re.MULTILINE)
            if len(parts) != 3 or parts[0].strip():
                raise ValueError("missing frontmatter")
            frontmatter = yaml.safe_load(parts[1])
            trigger = frontmatter.get("trigger") or {} if isinstance(frontmatter, dict) else None
            if not isinstance(trigger, dict):
                raise ValueError("invalid trigger object")
            existing = trigger.get("keywords") or []
            if not isinstance(existing, list) or not all(isinstance(value, str) and value.strip() for value in existing):
                raise ValueError("invalid keyword list")
            previous[path.resolve()] = {value.strip().casefold() for value in existing}
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise TriggerPolicyError(f"trigger_inventory_invalid: review existing role skill {path.name}") from exc
    others = {path: words for path, words in previous.items() if path != current}
    occupied = set().union(*others.values())
    # At least one unique term is mandated by the existing vault norm, not a
    # newly chosen quality threshold. Comparisons follow case-insensitive recall.
    if not keywords - occupied:
        raise TriggerPolicyError("trigger_keywords_not_unique: no audited keyword is unique in this role directory")
    # Preserve existing keyword cards that were distinguishable before this
    # operation. Old self participates in the before-pool when updating a card;
    # always/file-pattern-only entries and preexisting collisions need no repair.
    before_counts = Counter(word for words in previous.values() for word in words)
    after_counts = Counter(word for words in others.values() for word in words)
    after_counts.update(keywords)
    for path, words in others.items():
        if any(before_counts[word] == 1 for word in words) and not any(after_counts[word] == 1 for word in words):
            raise TriggerPolicyError(
                f"trigger_existing_keyword_uniqueness_lost: publication removes the last unique keyword of {path.name}"
            )


def patch_role_gene(role: str, new_skill_filename: str, dry_run: bool = False,
                    domain: str = "se") -> dict:
    """Compatibility no-op: directory + trigger is the only skill registry."""
    return {"ok": True, "reason": "dynamic_trigger", "gene_changed": False}


def copy_skill_to_vault(routed: RoutedSkill, draft: dict, dry_run: bool = False) -> dict:
    """Write/verify a generated pair; authorization is enforced by inject_one."""
    snapshots = []
    try:
        skill, sidecar = validate_destinations(routed)
        assert_owned_pair(skill, draft)
        content = render_vault_skill(routed, draft)
        if dry_run:
            return {"ok": True, "dry_run": True, "new_path": str(skill), "preview": content}
        snapshots = [snapshot(skill), snapshot(sidecar)]
        atomic_write_text(skill, content)
        if skill.read_text(encoding="utf-8") != content:
            raise ValueError("Final Markdown differs from the rendered audited card")
        envelope = {"artifact_sha256": sha256_file(skill), "draft": evidence_draft(draft)}
        atomic_write_text(sidecar, json.dumps(envelope, ensure_ascii=False, indent=2) + "\n")
        checked = renderer_module().load_artifact_evidence(skill)
        if canonical_json(checked["draft"]) != canonical_json(evidence_draft(draft)):
            raise ValueError("Final evidence differs from canonical audited card")
        return {"ok": True, "new_path": str(skill), "artifact_sha256": checked["artifact_sha256"],
                "evidence_sha256": sha256_file(sidecar)}
    except Exception as exc:
        if snapshots:
            restore_snapshots(snapshots)
        return {"ok": False, "error": str(exc), "new_path": str(routed.new_path)}


# ---------------------------------------------------------------------------
# 复盘日志
# ---------------------------------------------------------------------------

def write_retrospective_log(
    injected: list[tuple[RoutedSkill, dict]],
    gene_patches: list[dict],
) -> None:
    """追加复盘记录:每次注入写一条。"""
    gene_patches = [patch for patch in gene_patches if patch.get("gene_changed")]
    today = date.today().isoformat()
    _retrospective_dir().mkdir(parents=True, exist_ok=True)
    log_file = _retrospective_dir() / f"{today}-角色注入.md"
    with log_file.open("a", encoding="utf-8") as f:
        f.write(f"\n## skill-mind-distill run @ {time.strftime('%H:%M:%S')}\n\n")
        f.write(f"- 注入条数: {len(injected)}\n")
        f.write(f"- 修改的角色基因文件: {len(gene_patches)}\n\n")
        f.write("### 新增 skill\n\n")
        for rs, _ in injected:
            f.write(f"- `{rs.new_filename}` → {rs.role}\n")
        f.write("\n### 角色基因 frontmatter 改动\n\n")
        for patch in gene_patches:
            f.write(f"- `{patch['relpath']}`: skill_refs {len(patch.get('prev_skill_refs', []))} → {len(patch.get('new_skill_refs', []))}\n")
        f.write("\n")


# ---------------------------------------------------------------------------
# 编排:对单条 routed + draft 走完整 inject 流程
# ---------------------------------------------------------------------------

def inject_one(routed: RoutedSkill, draft: dict, dry_run: bool = False) -> dict:
    """Authorize dynamic recall before committing the Markdown/evidence pair."""
    snapshots = []
    stage = "guard"
    try:
        skill, sidecar = validate_destinations(routed)
        validate_audit(routed, draft)
        assert_owned_pair(skill, draft)
        stage = "trigger"
        _validate_trigger_pool(routed, draft)
        patch = patch_role_gene(routed.role, routed.new_filename, dry_run=True, domain=routed.domain)
        if dry_run:
            copied = copy_skill_to_vault(routed, draft, dry_run=True)
            return {"ok": copied["ok"], "stage": "copy", "copy": copied, "patch": patch}
        snapshots = [snapshot(path) for path in (skill, sidecar)]
        operation_id = uuid4().hex
        stage = "copy"
        copied = copy_skill_to_vault(routed, draft)
        if not copied["ok"]:
            raise ValueError(copied.get("error", "Publication pair write failed"))
        # Verify the actual final pair before committing its recoverable state.
        renderer_module().load_artifact_evidence(skill)
        if sha256_file(skill) != copied["artifact_sha256"] or sha256_file(sidecar) != copied["evidence_sha256"]:
            raise ValueError("Publication pair changed during transaction")
        stage = "ledger"
        append_ledger({"action": "publication_committed", "status": "ok", "run_id": operation_id,
                       "operation_id": operation_id, "relpath": skill.relative_to(_vault_root()).as_posix(),
                       "role": routed.role, "uuid": draft.get("uuid", ""),
                       "artifact_sha256": copied["artifact_sha256"], "evidence_sha256": copied["evidence_sha256"],
                       "files": complete_snapshots(snapshots)})
        return {"ok": True, "copy": copied, "patch": patch, "run_id": operation_id}
    except Exception as exc:
        rollback_error = None
        if snapshots:
            try:
                restore_snapshots(snapshots)
            except Exception as rollback_exc:
                rollback_error = str(rollback_exc)
        result = {"ok": False, "stage": stage, "error": str(exc)}
        if isinstance(exc, TriggerPolicyError):
            result["needs_human_review"] = True
        if rollback_error:
            result["rollback_error"] = rollback_error
        return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse
    from rich.console import Console

    parser = argparse.ArgumentParser(description="阶段 5: 写入动态 trigger 技能及证据")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    console = Console()
    routed_path = _common.STATE_DIR / "routed.jsonl"
    if not routed_path.exists():
        console.print(f"[red]找不到 {routed_path},先跑阶段 4[/red]")
        return 1

    injected: list[tuple[RoutedSkill, dict]] = []
    patches: list[dict] = []
    failed: list[dict] = []

    with routed_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rd = json.loads(line)
                routed = RoutedSkill(
                    draft_path=rd["draft_path"],
                    role=rd["role"],
                    prefix=rd["prefix"],
                    next_n=rd["next_n"],
                    new_filename=rd["new_filename"],
                    new_path=Path(rd["new_path"]),
                    target_subdir=Path(rd["target_subdir"]),
                    domain=rd.get("domain", "se"),
                    audit=rd.get("audit", {}),
                )
                draft = _load_draft(routed.draft_path)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                console.print(f"[red]路由记录或草稿无效: {type(exc).__name__}[/red]")
                failed.append({"error": "invalid routed record or draft"})
                continue
            if not draft:
                console.print(f"[red]草稿丢失: {routed.draft_path}[/red]")
                failed.append({"routed": rd, "error": "draft missing"})
                continue

            result = inject_one(routed, draft, dry_run=args.dry_run)
            if result["ok"]:
                injected.append((routed, draft))
                patches.append(result["patch"])
                console.print(
                    f"  [green]✓[/green] {routed.new_filename} → {routed.role}"
                )
            else:
                console.print(
                    f"  [red]✗ {routed.new_filename} 失败 @ {result['stage']}: {result.get('error')}[/red]"
                )
                failed.append({"routed": rd, "error": result.get("error"), "stage": result.get("stage")})

    if not args.dry_run and injected:
        write_retrospective_log(injected, patches)

    console.print(
        f"\n[bold]inject 完成[/bold] 成功 {len(injected)} / 失败 {len(failed)}"
    )
    return 0 if not failed else 1


def _load_draft(draft_path: str) -> dict | None:
    if not draft_path or not Path(draft_path).exists():
        return None
    return json.loads(Path(draft_path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    sys.exit(main())
