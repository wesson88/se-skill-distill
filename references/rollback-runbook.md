# 回滚 Runbook

## 何时触发

| 场景 | 触发方式 | 范围 |
|---|---|---|
| 阶段 4 partial 失败(写 vault 文件 / 改 frontmatter 部分成功) | **自动** — 5 阶段之间检测 | 全部已写 vault 文件 + 已改 frontmatter |
| 用户主动撤销上一次运行 | `python 05_rollback.py --run-id <id>` | 同上 |
| 跑前想撤销某次 | `python 05_rollback.py --run-id <id>` | 同上 |
| 系统级崩溃(partial 数据损坏) | 手动,见下方 | 配合 .bak 恢复 |

## Ledger 文件

`scripts/_state/injection_ledger.jsonl`,每行一条 JSON:

```json
{
  "run_id": "2026-07-02T10:23:11",
  "ts": "2026-07-02T10:23:11.234Z",
  "action": "skill_file_copied" | "frontmatter_patched" | "frontmatter_restored",
  "relpath": "20-知识/角色技能/se/后端工程师/B8-xxx.md",
  "expected_diff": "...",
  "status": "ok" | "failed"
}
```

回滚时按时间倒序读 ledger,逐条 revert。

## 自动回滚流程

```
阶段 4 partial 失败
  ↓
读 ledger
  ↓
对每个 status=ok 的 frontmatter_patched:
  - shutil.copy2({relpath}.bak, {relpath})  # 从 .bak 恢复
  - 在 ledger 写 frontmatter_restored
  ↓
对每个 status=ok 的 skill_file_copied:
  - os.remove(relpath)
  - 在 ledger 写 skill_file_removed
  ↓
回滚完成后:
  - 退出码 != 0,console 提示"已自动回滚,详见 _state/injection_ledger.jsonl"
```

## 手动回滚(用户主动撤销)

```bash
# 查看最近运行
python scripts/pipeline.py --status

# 撤销指定 run_id
python scripts/05_rollback.py --run-id 2026-07-02T10:23:11
```

## 系统崩溃后手动恢复

如果 ledger 损坏或 .bak 丢失,按以下步骤恢复:

### 1. 找最近一次的 .bak 文件

```bash
# 列出所有 .bak(按修改时间倒序)
ls -lt 00-系统/角色基因/se/*.bak
```

### 2. 选最新 .bak 之前的 .md 看哪个版本是"已知好的"

```bash
git log --oneline -- 00-系统/角色基因/se/角色-后端工程师.md
```

### 3. 用 git 还原

```bash
# 还原角色基因
git restore 00-系统/角色基因/se/角色-后端工程师.md

# 还原新写的 skill 文件(从 .bak 找 backup)
# 如果 .bak 没有 skill 文件副本(本 skill 只 backup 角色基因),用 git:
git status 20-知识/角色技能/se/后端工程师/
# 找到本轮新加的 .md 文件,git restore 删除
```

### 4. 清理 .bak

```bash
# 恢复成功后,删 .bak
rm 00-系统/角色基因/se/角色-后端工程师.md.bak
```

## 保留策略

- `.bak` 文件保留 30 天
- `injection_ledger.jsonl` 保留 90 天
- 过期后由 `scripts/_state/cleanup.py` 兜底(未实现,人工清理)

## 失败时不要做的事

❌ **不要**直接 `rm 角色-{X}.md` 然后重新写 — 会丢 .bak 之前的版本控制历史
❌ **不要**改 `injection_ledger.jsonl` — 是 audit 凭证
❌ **不要**跳过回滚 — 已知 broken 状态会让下次运行雪崩
✅ **总是**先跑 `git status` 看清楚当前状态再操作
