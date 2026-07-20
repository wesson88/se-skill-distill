# 90% 阈值审计规则

## 目标

`audit_extract(source_hash, draft) -> AuditResult`,核心字段 `verdict` ∈ {`PASS`, `FAIL`}。

## 判定

读取 `skillmind.auditor.audit_source()` 的 `coverage_weighted`(0-100):

```
if coverage >= 90%:
    verdict = "PASS"
    needs_human_review = False
else:
    verdict = "FAIL"
    needs_human_review = True
```

| coverage | verdict | needs_human_review | 下游动作 |
|---|---|---|---|
| ≥ 90% | `PASS` | `false` | 自动放行(进入 route / inject / publish) |
| < 90% | `FAIL` | `true` | 走人工 review `missing[]` 列表后回归抽取 |

## 数据来源

`skillmind.auditor.audit_source()` 的精简 `AuditReport.to_summary_dict()`:

```python
{
    "source_hash": "b0c4837e...",
    "source_title": "SKILL.md",
    "coverage": 96.3,
    "verdict": "PASS",
    "needs_human_review": False,
    "missing": [
        {"section": "...", "statement": "...", "reason": "..."},
        ...
    ]
}
```

`missing[]` 每条 `{section, statement, reason}` — `reason` 来自 Pass 2 LLM,说明该 items 在 source 哪一节、extract 为什么漏(没提到 / 简化掉 / 放错章节)。

## audit 内部流程(skillmind)

1. **Pass 1(LLM,~3-10s)** — 从 source 按 `##/###` 章节抽可验证条目(items),每条 ≤ 30 字陈述 + section 引用
2. **Pass 2(LLM,~5-15s)** — 对每个 item 逐条 vs extract 卡片:
   - `complete` — extract 中有 verbatim 保留
   - `weak` — 有但简化 / 改写
   - `missing` — 完全找不到(强制给 reason)
3. **规则扫** — 数字 / URL / wikilink 反查(0 halluc 是底线)+ 跨卡重复(多卡时,跳过 frontmatter)

coverage = `(complete + 0.5*weak) / total * 100`

## 上游设置建议

```yaml
# ~/.skillmind/config.yaml
llm_profiles:
  extract:
    model: deepseek/deepseek-chat        # 提取模型
  audit:
    model: anthropic/claude-3-5-haiku  # 故意不同模型(交叉验证,避免自审自)
```

## 给本层(`se-skill-distill`)的接口

```python
from skillmind.auditor import audit_source
r = audit_source(source_hash, cfg)
# r.coverage_weighted (0-100)
# r.verdict          ('PASS' / 'FAIL')
# r.needs_human_review (bool)
# r.missing         (list of {section, statement, reason})

# 本层不再重算 score,不再读 kind_coverages / score_breakdown 等旧字段
# 简化 AuditResult 字段:coverage / needs_human_review / missing / verdict / notes
```

## 历史

- v1 (4 维评分 `0.55/0.20/0.15/0.10` + approved/review/rejected 3 档)— 已废弃,heuristic 模式对长文低估
- v2 (kind 加权 score + 同 3 档)— 已废弃,LLM 主观打分漂移大
- **v3 (本版,90% 阈值 + PASS/FAIL 2 档)** — 简化为单阈值二元判定,verdict 由 skillMind LLM Pass 1+2 给出
