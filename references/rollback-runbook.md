# 入库事务与回滚（0.2）

每次 `inject_one` 将角色 Markdown 与邻接 `.evidence.json` 视为同一操作。写入失败时尝试恢复操作前状态；成功后记录 `publication_committed`。当前 vault 由目录与 trigger 动态召回，正常入库不改写或创建角色基因。所有路径必须位于选定 vault 内，已有文件的覆写须有本工具的 ledger 所有权和匹配哈希。

## 查看与撤销

先使用与入库时一致的 Python、`SE_VAULT`、`SE_STATE_DIR`，再运行：

```bash
python scripts/05_rollback.py --list
python scripts/05_rollback.py --run-id <操作ID> --dry-run
python scripts/05_rollback.py --run-id <操作ID>
```

不传 `--run-id` 时选择最近一个未回滚操作。操作使用唯一 ID，不能凭相同秒的时间戳将多个操作混为一组。一个流水线批次可能含多个入库操作，应按操作 ID 选择；反向更新旧文件时按最新操作向前撤销。

## 账本与恢复依据

默认账本为 `_state/injection_ledger.jsonl`，可通过 `SE_STATE_DIR` 移到本机私有状态目录。`publication_committed` 包含：

- `run_id` / `operation_id`、角色、UUID、相对文件路径。
- 最终 Markdown 与证据文件的哈希。
- `files` 中各成员的操作前内容快照及前后哈希；原本不存在的文件记录空的前状态。

状态快照可能含私有角色内容，只保存在本机，默认状态目录被 git 忽略。不要手工改写账本来绕过验证。

回滚先验证所有成员的当前字节仍匹配操作后的哈希，再恢复原内容或移除本次新建文件。正文与证据一起处理；记录 `publication_rolled_back` 后不重复撤销。若文件被手工修改、移走、缺少恢复依据或路径越界，会拒绝自动覆盖/删除，保留人工核对。

历史 ledger 可保留查询，但缺少所有权/前后哈希时不能安全自动恢复。旧 `.bak` 不作为无条件覆盖当前文件的授权。请从个人备份和原始记录人工核对，不要使用递归删除或复制覆盖来模拟回滚。
