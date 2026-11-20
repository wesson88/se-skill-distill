# 审计结果消费契约（0.2）

SE 层不计算置信度或覆盖阈值，直接消费 SkillMind v2.5 返回的语义结论，并验证产物对应关系。

| verdict | 处理 |
|---|---|
| PASS，且 `needs_human_review: false` | 证据完整时允许路由/入库；语义 Skill 还需四维均 PASS |
| FAIL | 写人工复核，保留明确失败与缺口，禁止入库 |
| UNKNOWN | 写人工复核，保留未完成/格式/证据/运行错误，禁止入库 |

四维为 `coverage_status`、`support_status`、`synthesis_status`、`source_inventory_status`。分别检查原文内容覆盖、输出主张的来源支持、知识重组和来源台账完整性。覆盖率是显示字段，可为 null；数字满分不代表四维都通过，也不能用旧百分比规则覆盖 verdict。

`AuditResult` 保留原有来源、角色、missing 和 notes，并增加 schema/audit 版本、四维状态、完整 `audit_details`、全部 `draft_paths`、草稿 `draft_sha256`、暂存 `artifact_paths` 与 `artifact_sha256`。只有来源草稿、验证过的暂存证据和 `report.extract_files` 完全对应才可批准。审计前后文件变化、缺卡或证据替换使结果不可批准。

来源存在有效失败维度时，SkillMind 可能同时返回 FAIL 与其他维度的 `evaluation_errors`。保留该明确 FAIL 和详细错误；不能清空为“未发现问题”。对矛盾或不完整的 PASS 按 UNKNOWN 处理。

非 Skill 文档继续使用当前 SkillMind 的原有审计，其 legacy 结果不伪装为新语义四维 PASS。缺少当前证据的旧 Skill 需要重新提炼，不能套用旧覆盖分数放行。

最终角色文件重新绑定 `{artifact_sha256, draft}` 证据信封。入库前验证批准记录、源哈希、草稿字节和内容；渲染保留规范正文，回滚同时处理正文与证据。最终 type 为 skill，触发词取自已审数据，并按当前 vault §10.4 检查目录内可区分性；不向角色基因添加 refs。哈希证明文件一致性，不是语义真假的证明。
