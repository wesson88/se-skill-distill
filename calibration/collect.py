"""
Calibration 收集 — 直接调 audit_source 两遍(LLM + embedding),不通过 pipeline。

前提: vault/skills/ 下有 5-10 源的草稿(需先 install + extract + publish)。
用法:
    # 第一步:让 5-10 源完成 install + extract + publish
    python scripts/pipeline.py --config sources.yaml --skip-audit  # TODO

    # 第二步:本脚本,两遍 audit
    python scripts/calibration/collect.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from skillmind.auditor import audit_source
from skillmind.collector import list_cached
from skillmind.config import load_config, get_vault_dir
from skillmind.extractor import extract_skill as _extract
from skillmind.collector import ingest_url

# Calibration 源
SOURCES = [
    ("fastapi",              "https://raw.githubusercontent.com/tiangolo/fastapi/master/README.md", "后端工程师"),
    ("awesome-fastapi",      "https://raw.githubusercontent.com/mjhea0/awesome-fastapi/master/README.md", "后端工程师"),
    ("errhandling",          "https://raw.githubusercontent.com/goldbergyoni/nodebestpractices/master/sections/errorhandling/README.md", "后端工程师"),
    ("ddd-hexagon",          "https://raw.githubusercontent.com/Sairyss/domain-driven-hexagon/master/README.md", "后端工程师"),
    ("flask",                "https://raw.githubusercontent.com/pallets/flask/master/README.md", "后端工程师"),
    ("awesome-react",        "https://raw.githubusercontent.com/enaqx/awesome-react/master/README.md", "前端工程师"),
    ("clean-code-js",        "https://raw.githubusercontent.com/ryanmcdermott/clean-code-javascript/master/README.md", "前端工程师"),
    ("system-design",        "https://raw.githubusercontent.com/donnemartin/system-design-primer/master/README-zh-Hans.md", "架构师"),
]

OUT = Path("D:/Markdown/memory/adam/.claude/skills/se-skill-distill/calibration/calibration_data.jsonl")


def ensure_published(url: str) -> str:
    """确保源已 ingest + extract + publish。返回 source_hash。"""
    cached = {c.get("source_url"): c for c in list_cached()}
    if url in cached:
        sh = cached[url]["source_hash"]
    else:
        results = ingest_url(url)
        sh = results[0]["source_hash"]
        # extract
        info = next(c for c in list_cached() if c["source_hash"] == sh)
        _extract(info["raw_path"], info, load_config())
    # publish
    from skillmind.reviewer import list_drafts
    from skillmind.renderer import publish_to_vault
    drafts = [d for d in list_drafts() if d.get("source", {}).get("source_hash") == sh and d.get("status") != "published"]
    for d in drafts:
        publish_to_vault(d, cfg=load_config(), output_dir=str(get_vault_dir(load_config()) / "skills"))
    return sh


def audit_both_modes(source_hash: str) -> tuple[float, float, int, int, int]:
    """两遍 audit,返回 (emb_complete, llm_complete, total, emb_complete_count, llm_complete_count)。"""
    cfg_base = load_config()
    # 1. embedding 模式(默认)
    rep_emb = audit_source(source_hash, cfg=cfg_base, console=None, vault_skills_override=str(get_vault_dir(cfg_base) / "skills"))
    emb_complete = rep_emb.complete_count / max(len(rep_emb.matches), 1)
    # 2. LLM 模式
    cfg_llm = {**cfg_base, "audit": {"use_llm_pass2": True}}
    rep_llm = audit_source(source_hash, cfg=cfg_llm, console=None, vault_skills_override=str(get_vault_dir(cfg_llm) / "skills"))
    llm_complete = rep_llm.complete_count / max(len(rep_llm.matches), 1)
    return emb_complete, llm_complete, len(rep_emb.matches), rep_emb.complete_count, rep_llm.complete_count


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("", encoding="utf-8")
    for name, url, role in SOURCES:
        print(f"\n=== {name} ===", flush=True)
        try:
            sh = ensure_published(url)
            emb, llm, total, emb_ok, llm_ok = audit_both_modes(sh)
            data = {
                "name": name, "url": url, "role": role,
                "total_atomic": total,
                "emb_complete_rate": round(emb, 4),
                "llm_complete_rate": round(llm, 4),
                "emb_complete_count": emb_ok,
                "llm_complete_count": llm_ok,
            }
            with OUT.open("a", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False) + "\n")
            print(f"  total={total}  emb_complete={emb:.3f}  llm_complete={llm:.3f}", flush=True)
        except Exception as e:
            print(f"  FAIL: {e}", flush=True)


if __name__ == "__main__":
    main()
