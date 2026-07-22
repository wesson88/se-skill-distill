"""
阶段 5: Inject — 写 vault skill 文件 + 改角色基因 frontmatter

关键能力:
- 原子写 vault skill 文件
- 改 frontmatter: skill_refs / rule_refs 追加(用 ruamel.yaml 保注释)
- 原角色基因 .bak 备份
- ledger 记录每次操作,partial 失败时由 05_rollback 还原
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

import _common
from _common import (
    LEDGER_FILE,
    RoutedSkill,
    STATE_DIR,
    append_ledger,
    atomic_write_text,
    create_role_gene_template,
    ensure_role_dirs,
    role_gene_file,
)


# vault 路径在 set_vault_root() 后会改,这里用函数动态查
def _role_gene_dir() -> Path:
    return _common.ROLE_GENE_DIR


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
    # type: draft.meta.type(可能是 list),取第一个
    type_field = meta.get("type", [])
    if isinstance(type_field, list):
        type_field = type_field[0] if type_field else "concept-explanation"

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
    trig_kws: list[str] = []
    tk = meta.get("trigger_keywords") or []
    if isinstance(tk, list):
        trig_kws = [str(x) for x in tk if str(x).strip()]
    if not trig_kws:
        tags = le.get("knowledge_tags") or []
        if isinstance(tags, list):
            trig_kws = [str(x) for x in tags if str(x).strip()]
    if role and role not in trig_kws:
        trig_kws.append(role)  # 角色名也作关键词,便于按角色触发

    fm_lines: list[str] = ["---"]
    fm_lines.append(f"role: {role}")
    fm_lines.append(f"skill_id: {skill_id}")
    fm_lines.append(f"type: {type_field}")
    fm_lines.append(f"lifecycle: NEW")
    fm_lines.append("trigger:")
    fm_lines.append("  always: false")
    fm_lines.append("  keywords:")
    for kw in trig_kws[:8]:
        fm_lines.append(f"    - {json.dumps(kw, ensure_ascii=False)}")
    fm_lines.append("  file_patterns: []")
    fm_lines.append(f"uuid: {draft.get('uuid', '')}")
    fm_lines.append(f"source_url: {source.get('source_url') or source.get('repo_url', '')}")
    fm_lines.append(f"source_hash: {source.get('source_hash', '')}")
    fm_lines.append(f"source_reliability: {draft.get('source_reliability', 'medium')}")
    fm_lines.append(f"obsolescence_risk: {draft.get('obsolescence_risk', 'medium')}")
    fm_lines.append(f"doc_type: {source.get('doc_type', 'blog')}")
    fm_lines.append("---")
    fm_lines.append("")
    fm = "\n".join(fm_lines)

    return fm + "\n" + body


# ---------------------------------------------------------------------------
# 角色基因 frontmatter 改写
# ---------------------------------------------------------------------------

def patch_role_gene(role: str, new_skill_filename: str, dry_run: bool = False, domain: str = "se") -> dict:
    """
    改 角色-{role}.md 的 frontmatter:
    - skill_refs 追加 '20-知识/角色技能/{domain}/{role}/{new_skill_filename}.md'
    - rule_refs 追加 '[[{new_skill_filename}#3. 核心约束]]'
    基因文件不存在时自动按模板创建(新角色零配置)。
    原子写 + .bak 备份 + ledger 记录。
    返回 {ok, relpath, prev_refs, new_refs, prev_rule_refs, new_rule_refs}

    若 dry_run=True,只返回 diff 不写盘。
    """
    gene_file = role_gene_file(role, domain)
    if not gene_file.exists():
        if dry_run:
            return {"ok": False, "error": f"角色基因文件不存在(dry-run 不自动建): {role} → {gene_file}"}
        create_role_gene_template(role, domain)

    new_skill_ref = f"20-知识/角色技能/{domain}/{role}/{new_skill_filename}.md"
    new_rule_ref = f"[[{new_skill_filename}#3. 核心约束]]"

    if dry_run:
        # 只读 frontmatter 报告
        text = gene_file.read_text(encoding="utf-8")
        return {
            "ok": True,
            "dry_run": True,
            "relpath": str(gene_file.relative_to(_vault_root())),
            "would_add_skill_ref": new_skill_ref,
            "would_add_rule_ref": new_rule_ref,
            "current_skill_refs": _extract_field(text, "skill_refs"),
            "current_rule_refs": _extract_field(text, "rule_refs"),
        }

    # 真实改写:用 ruamel.yaml
    try:
        from ruamel.yaml import YAML
    except ImportError:
        return {"ok": False, "error": "ruamel.yaml 未安装,跑: pip install ruamel.yaml"}

    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096

    # 备份
    backup_path = gene_file.with_suffix(gene_file.suffix + ".bak")
    shutil.copy2(gene_file, backup_path)

    # 读 + 改
    text = gene_file.read_text(encoding="utf-8")
    # 文件结构:`---\nfrontmatter\n---\nmarkdown body\n`,用 split 取 frontmatter 单独解析
    # 避免 ruamel 把 markdown body 当成第二个 YAML doc 报 multi-doc 错
    import re as _re
    parts = _re.split(r'^---\s*$', text, maxsplit=2, flags=_re.MULTILINE)
    # parts 长度: 3 = ['', frontmatter_yaml, markdown_body]
    if len(parts) < 3:
        return {"ok": False, "error": f"{gene_file} 不是标准 frontmatter 文件格式"}

    frontmatter_text = parts[1].strip()
    body = parts[2]  # 保留原 markdown body
    try:
        data = yaml.load(frontmatter_text)
    except Exception as e:
        return {"ok": False, "error": f"解析 frontmatter 失败: {e}"}

    if data is None or not isinstance(data, dict):
        return {"ok": False, "error": f"frontmatter 为空或非 dict: {gene_file}"}

    prev_skill_refs = list(data.get("skill_refs", []) or [])
    prev_rule_refs = list(data.get("rule_refs", []) or [])

    new_skill_refs = list(prev_skill_refs)
    if new_skill_ref not in new_skill_refs:
        new_skill_refs.append(new_skill_ref)
    new_rule_refs = list(prev_rule_refs)
    if new_rule_ref not in new_rule_refs:
        new_rule_refs.append(new_rule_ref)

    data["skill_refs"] = new_skill_refs
    data["rule_refs"] = new_rule_refs

    # 输出:重新拼装 `--- new_frontmatter ---\n body`
    from io import StringIO
    buf = StringIO()
    yaml.dump(data, buf)
    new_text = f"---\n{buf.getvalue()}---\n{body}"

    # 原子写
    atomic_write_text(gene_file, new_text)

    # ledger
    relpath = str(gene_file.relative_to(_vault_root()))
    append_ledger({
        "action": "frontmatter_patched",
        "relpath": relpath,
        "added_skill_ref": new_skill_ref,
        "added_rule_ref": new_rule_ref,
        "prev_skill_refs_count": len(prev_skill_refs),
        "new_skill_refs_count": len(new_skill_refs),
        "status": "ok",
    })

    return {
        "ok": True,
        "relpath": relpath,
        "prev_skill_refs": prev_skill_refs,
        "new_skill_refs": new_skill_refs,
        "prev_rule_refs": prev_rule_refs,
        "new_rule_refs": new_rule_refs,
    }


def _extract_field(text: str, field: str) -> list[str]:
    """简易 frontmatter 字段提取(用于 dry_run 报告)。"""
    in_fm = False
    lines = text.split("\n")
    out: list[str] = []
    in_field = False
    for line in lines:
        if line.strip() == "---":
            if in_fm:
                break
            in_fm = True
            continue
        if not in_fm:
            continue
        if line.startswith(f"{field}:"):
            rest = line[len(field) + 1:].strip()
            if rest.startswith("[") and rest.endswith("]"):
                inner = rest[1:-1]
                out = [x.strip().strip("'\"") for x in inner.split(",") if x.strip()]
                in_field = False
            else:
                in_field = True
        elif in_field:
            stripped = line.strip()
            if stripped.startswith("-"):
                out.append(stripped[1:].strip().strip("'\""))
            elif stripped and not stripped.startswith("-"):
                in_field = False
    return out


# ---------------------------------------------------------------------------
# 写 vault skill 文件
# ---------------------------------------------------------------------------

def copy_skill_to_vault(routed: RoutedSkill, draft: dict, dry_run: bool = False) -> dict:
    """
    渲染并原子写 vault skill 文件。
    返回 {ok, new_path, ...}
    """
    content = render_vault_skill(routed, draft)
    if dry_run:
        return {"ok": True, "dry_run": True, "new_path": str(routed.new_path), "preview": content[:500]}

    try:
        atomic_write_text(routed.new_path, content)
    except Exception as e:
        return {"ok": False, "error": str(e), "new_path": str(routed.new_path)}

    relpath = str(routed.new_path.relative_to(_vault_root()))
    append_ledger({
        "action": "skill_file_copied",
        "relpath": relpath,
        "role": routed.role,
        "status": "ok",
    })
    return {"ok": True, "new_path": str(routed.new_path)}


# ---------------------------------------------------------------------------
# 复盘日志
# ---------------------------------------------------------------------------

def write_retrospective_log(
    injected: list[tuple[RoutedSkill, dict]],
    gene_patches: list[dict],
) -> None:
    """追加复盘记录:每次注入写一条。"""
    today = date.today().isoformat()
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
    """对单条 routed 走 copy_skill_to_vault + patch_role_gene。"""
    copy_result = copy_skill_to_vault(routed, draft, dry_run=dry_run)
    if not copy_result["ok"]:
        return {"ok": False, "stage": "copy", "error": copy_result.get("error")}

    patch_result = patch_role_gene(routed.role, routed.new_filename, dry_run=dry_run, domain=routed.domain)
    if not patch_result["ok"]:
        return {"ok": False, "stage": "patch", "error": patch_result.get("error"), "copy_result": copy_result}

    return {"ok": True, "copy": copy_result, "patch": patch_result}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse
    from rich.console import Console

    parser = argparse.ArgumentParser(description="阶段 5: 写 vault + 改 frontmatter")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    console = Console()
    routed_path = Path("_state/routed.jsonl")
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
            )
            draft = _load_draft(routed.draft_path)
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
