"""把 skillmind 审计结果译成 se 的放行决定。不调用模型。

只认 verdict。coverage 过线不能单独放行。UNKNOWN 不是 PASS。
幻觉检查没跑时，rate 是 None，不能写成 0。
"""

from __future__ import annotations

from typing import Any


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _read(report: Any, key: str, default: Any = None) -> Any:
    if isinstance(report, dict):
        return report.get(key, default)
    return getattr(report, key, default)


def interpret_report(report: Any) -> dict:
    """接受 skillmind 的 AuditReport 对象，或它的 summary JSON。"""
    summary = _as_dict(_read(report, "summary"))
    if isinstance(report, dict) and "verdict" in report:
        summary = report
    verdict = str(_read(report, "verdict") or summary.get("verdict") or "UNKNOWN")
    if verdict not in ("PASS", "FAIL", "UNKNOWN"):
        verdict = "UNKNOWN"

    completeness = _as_dict(summary.get("completeness") or _read(report, "completeness"))
    executability = _as_dict(summary.get("executability") or _read(report, "executability"))
    hallucination = _as_dict(summary.get("hallucination"))
    if "checked" in hallucination:
        checked = bool(hallucination.get("checked"))
        count = hallucination.get("count") if checked else None
    elif not isinstance(report, dict) and hasattr(report, "hallucinations"):
        checked = True
        count = len(getattr(report, "hallucinations") or [])
    else:
        checked = False
        count = None

    items = _read(report, "items") or []
    rate = None
    if checked and count is not None:
        rate = count / max(len(items), 1)

    if isinstance(report, dict):
        coverage = report.get("coverage")
    else:
        coverage = getattr(report, "coverage_reported", None)
        if coverage is None:
            coverage = getattr(report, "coverage_weighted", None)

    notes: list[str] = []
    if verdict != "PASS":
        notes.append(
            "verdict={verdict} completeness={comp} executability={exe} faithfulness={faith}".format(
                verdict=verdict,
                comp=completeness.get("status"),
                exe=executability.get("status"),
                faith=executability.get("faithfulness"),
            )
        )
    if not checked:
        notes.append("hallucination check did not run; rate is not 0")
    elif count:
        notes.append(f"hallucinations {count} 条,数字/wikilink 失真")

    return {
        "verdict": verdict,
        "needs_human_review": verdict != "PASS",
        "coverage": coverage,
        "hallucination_checked": checked,
        "hallucination_count": count if checked else None,
        "hallucination_rate": rate,
        "completeness_status": completeness.get("status"),
        "executability_status": executability.get("status"),
        "faithfulness": executability.get("faithfulness"),
        "quality_current": summary.get("quality_current") if isinstance(summary, dict) else None,
        "notes": notes,
        "route": "approved" if verdict == "PASS" else "manual-review",
    }


def attach_yaml_roles(cached: list[dict], sources: list[dict]) -> None:
    """skip-extract 时 list_cached 没有 target_role。按目录名把 sources.yaml 的角色补回去。"""
    for item in cached:
        path = str(item.get("source_path") or item.get("raw_path") or "")
        for src in sources:
            src_path = str(src.get("path") or "").rstrip("/\\")
            if not src_path:
                continue
            leaf = src_path.replace("\\", "/").rstrip("/").split("/")[-1]
            if src_path in path or (leaf and leaf in path.replace("\\", "/")):
                if src.get("target_role"):
                    item["target_role"] = src["target_role"]
                if src.get("notes") and not item.get("notes"):
                    item["notes"] = src["notes"]
                break
