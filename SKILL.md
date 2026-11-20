---
name: se-skill-distill
description: 当用户要求把 GitHub Skill、README、博客或论坛经验沉淀到角色技能库，或需要检查已有蒸馏来源的审计、角色路由、入库与回滚状态时使用。
---

# SE 角色技能蒸馏

通过当前 Python 环境中的 SkillMind v2.5 生成和核验知识，再路由到角色技能库。运行前读取 [README](README.md) 的环境与副作用说明；本技能使用 `semantic_distillation_v1`，不适用于仅支持旧覆盖审计的运行环境。

## 入口

```bash
python scripts/preflight.py --vault <vault根目录>
python scripts/distill_url.py <GitHub链接> --role 技术主管 --vault <vault根目录>
python scripts/pipeline.py --config sources.yaml --vault <vault根目录>
```

来源与角色需明确。GitHub 单文件 `SKILL.md` / `*.skill.md` 应走 Skill 采集，普通 README/博客按文档处理。支持的来源配置见 [source-schema.md](references/source-schema.md)。

## 必须保持的契约

- 新 Skill 正文直接采用 SkillMind 已提炼的规范章节与内容，保留原理、示例、例外、依赖及执行细节；不要在 SE 层再总结、翻译或套回旧字段。
- 采用 SkillMind 的 `PASS/FAIL/UNKNOWN`，保留完整理由与四维结果。只有有效 PASS 且无需人工复核的完整证据产物可继续入库；高覆盖数字不能代替该判断。
- UNKNOWN 表示尚未可靠完成核验，必须进入人工复核。不可将未测覆盖率补零、忽略异常或重新计算旧阈值后放行。已确认的 FAIL 不因其他维度未知而消失。
- 入库前核对草稿/暂存产物与审计绑定。最终 Markdown 必须保留正文，并携带绑定最终文件哈希的 `.evidence.json`；暂存审计通过不授权改写正文。
- 按当前 vault 角色基因规范 §10，以角色目录及技能自身 trigger 动态召回；不改写或创建基因，不自动登记 skill_refs 或 rule_refs。触发词来自已审元信息，须有本目录独有的词；冲突走复核，不臆造词或用角色名兜底。回滚同步处理证据，保护手工编辑文件。

## 结果处理

读取 `_state/audit/approved.jsonl` 与 `manual-review.jsonl`，向用户说明真实 verdict、具体缺口/异常及是否入库。有失败/未知来源时命令返回非零，不能宣称全流程成功。`SE_STATE_DIR` 可指定独立状态目录。

`--dry-run` 不写最终角色技能和角色基因，但仍可能调用模型并写暂存。`--skip-extract` 跳过提取，仍会审计。没有额外授权时不要替用户提交/推送、发布包或发送邮件。

详细审计契约见 [confidence-formula.md](references/confidence-formula.md)；回滚操作见 [rollback-runbook.md](references/rollback-runbook.md)。
