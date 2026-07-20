---
name: se-skill-distill
description: 当用户给出 GitHub skill URL(blob/tree/raw/owner-repo)并说"蒸馏它"或"把这个抽取到 vault"时使用,或在用户要求"把 SKILL.md / README / 博客 / 论坛等源蒸馏成 SE 域知识"时使用。本 skill 编排 6 阶段流水线(ingest → extract → audit → route → inject),通过 audit 质量校验(coverage ≥ 90% 自动入 vault,< 90% 需人工 review),自动改角色基因 frontmatter。适用于 SE 域(后端/前端/架构/技术主管/产品经理)的知识沉淀。可用 `python distill_url.py <URL>` 一键蒸馏单个 GitHub 源。
---

# se-skill-distill 使用说明

> **基于 skillMind 的 SE 域知识蒸馏流水线**

---

## 0. 30 秒上手

### 一键蒸馏(给 GitHub URL)

```bash
# 给任意 GitHub skill URL,自动解析 + 跑全流程
python distill_url.py https://github.com/Leonxlnx/taste-skill/blob/main/skills/brandkit/SKILL.md

# 短形式也行
python distill_url.py Leonxlnx/taste-skill

# 配角色(默认后端工程师)
python distill_url.py https://github.com/owner/repo --role 架构师
```

`distill_url.py` 支持的 URL 形式:
- 完整 blob URL → 转 raw URL
- 完整 tree URL → ingest 扫整目录
- 完整 raw URL → 直传
- 短形式 `owner/repo` → 扫整仓

跑完看 `_state/audit/approved.jsonl` 知道哪些源自动入了 vault,看 `_state/audit/manual-review.jsonl` 知道哪些需要人工 review。

### 批量蒸馏(改 sources.yaml)

```bash
# 1) 编辑 sources.yaml(列出要蒸馏的源)
# 2) 跑流水线
python pipeline.py --config sources.yaml
```

---

## 1. 怎么触发

| 方式 | 适用 |
|---|---|
| **Claude Code skill 自动发现**(本文件存在) | Claude 看到相关 prompt 时自动调用,例如用户说"蒸馏这个 README" / "把这篇博客萃到 vault" |
| **CLI 手动** | 调试 / CI / batch |
| **CI / scheduled** | `python pipeline.py --config sources.yaml`,exit code 0 = 全过 |

**Claude Code 触发本 skill 的典型 prompt 例子**:
- "把这个 brandkit README 蒸馏到 vault"
- "跑一下 se-skill-distill 流水线"
- "萃一下这篇博客,audit 看看覆盖率"
- "把 fastapi README 抽成 SE 域 skill"

---

## 2. CLI 完整参数

```bash
python pipeline.py --config <path> [选项]
```

| 参数 | 必填 | 说明 |
|---|---|---|
| `--config <path>` | ✅ | `sources.yaml` 路径 |
| `--vault <path>` | ❌ | vault 根路径(默认从脚本位置推导) |
| `--dry-run` | ❌ | 不写 vault,只跑流程 |
| `--skip-extract` | ❌ | 用 drafts/ 已存草稿,跳过 extract |
| `--skip-publish` | ❌ | 不 publish(已发布过) |
| `--status` | ❌ | 显示运行状态(ledger 概览) |

### 跨平台命令

| OS | 命令 |
|---|---|
| **Windows (PowerShell)** | `E:\workstation\ai\skillMind\.venv\Scripts\python.exe pipeline.py --config sources.yaml` |
| **Linux / macOS (bash)** | `./.venv/bin/python pipeline.py --config sources.yaml` |

Windows 建议前置 `$env:PYTHONIOENCODING = "utf-8"`,Linux/macOS 加 `export PYTHONIOENCODING=utf-8`。

---

## 3. sources.yaml 写法

```yaml
sources:
  - kind: single_url
    url: https://raw.githubusercontent.com/Leonxlnx/taste-skill/main/skills/brandkit/SKILL.md
    target_role: 后端工程师
    notes: "brandkit 蒸馏测试"
```

`kind` 可选:
- `single_url` — 任意 URL(博客/文档/单文件)
- `github_raw` — GitHub raw 文件
- `github_repo` — 整仓扫 SKILL.md
- `local_dir` — 本地目录递归扫
- `rss` — RSS feed

`target_role`: 后端工程师 / 前端工程师 / 架构师 / 技术主管 / 产品经理 / **auto**(关键词推断)

---

## 4. vault 布局要求

```
{VAULT_ROOT}/
├── 00-系统/
│   ├── 角色基因/se/
│   │   ├── 角色-后端工程师.md
│   │   ├── 角色-前端工程师.md
│   │   ├── 角色-架构师.md
│   │   ├── 角色-技术主管.md
│   │   └── 角色-产品经理.md
│   └── 复盘记录/{date}-角色注入.md
└── 20-知识/
    └── 角色技能/se/
        ├── 后端工程师/B<n>-<title>.md
        ├── 前端工程师/
        ├── 架构师/
        ├── 技术主管/
        └── 产品经理/
```

vault 根路径解析顺序:
1. `--vault` CLI 参数
2. `SE_VAULT` 环境变量
3. 从脚本位置推导(`<vault>/.claude/skills/se-skill-distill/scripts` → 上 3 层)

---

## 5. 6 阶段流水线

| # | 阶段 | 做什么 | 可跳过? |
|---|---|---|---|
| 1 | **ingest** | 抓源到 `~/.skillmind/cache/raw/` | ❌ |
| 2 | **extract** | LLM 抽草稿到 `~/.skillmind/drafts/` | `--skip-extract` |
| 2.5 | **publish** | drafts 写进 vault | `--skip-publish` |
| 3 | **audit** | 算 coverage,判 PASS/FAIL | ❌ |
| 4 | **route** | 按 role 选子目录,生成 `B<n>-<title>.md` | 若 audit FAIL 整体跳过 |
| 5 | **inject** | 写 skill + patch 角色基因 | ❌ |

**audit 判定**(stage 3 输出):

| coverage | verdict | needs_human_review | 后续 |
|---|---|---|---|
| ≥ 90% | `PASS` | `false` | 自动进 stage 4-5 |
| < 90% | `FAIL` | `true` | 停在该源,等人工 review `missing[]` |

---

## 6. 失败处理

### audit FAIL(coverage < 90%)

```bash
# 看哪些源 FAIL
cat _state/audit/manual-review.jsonl

# 看具体缺什么
cat _state/audit/manual-review.jsonl | python -m json.tool

# 修 extract prompt 后重跑
rm ~/.skillmind/cache/extract_cache/<source_hash>_extract_v9.json
python pipeline.py --config sources.yaml
```

### inject partial 失败

`04_inject.py` 出错时自动回滚(基于 `injection_ledger.jsonl` 记录)。

### 角色无法判定

写入 `_state/unknown-queue.md`。在 sources.yaml 显式设 `target_role` 重跑。

---

## 7. FAQ

**Q: Claude Code 怎么知道用这个 skill?**
A: 本目录的 SKILL.md frontmatter `description` 决定。Claude 看到相关请求(如"蒸馏这个 README")会匹配本 skill 自动调用。

**Q: 报"VAULT_ROOT 无效"**
A: vault 必须同时有 `00-系统/` 和 `20-知识/` 子目录。用 `--vault <path>` 或 `SE_VAULT=<path>` 显式指定。

**Q: 报"找不到 source_hash"**
A: 源没在 `~/.skillmind/hashes.yaml`。先跑 `python -m skillmind ingest <url>` 一次,再跑 pipeline。

**Q: 想用不同模型做 audit(交叉验证)**
A: 在 `~/.skillmind/config.yaml` 设 `llm_profiles.audit.model`:
```yaml
llm_profiles:
  extract: {model: deepseek/deepseek-chat}
  audit:   {model: anthropic/claude-3-5-haiku}
```

**Q: inject 报"角色基因文件不存在"**
A: 缺 `00-系统/角色基因/se/角色-<role>.md`。创建该文件(参考 references/naming-rules.md)。

---

## 8. 完整命令速查

```bash
# 跑
python pipeline.py --config sources.yaml

# 干跑
python pipeline.py --config sources.yaml --dry-run

# 跳过 extract
python pipeline.py --config sources.yaml --skip-extract

# 跳过 publish
python pipeline.py --config sources.yaml --skip-publish

# 显式 vault
python pipeline.py --config sources.yaml --vault "D:\my-vault"

# 状态
python pipeline.py --status

# 跨平台
# Windows:    E:\workstation\ai\skillMind\.venv\Scripts\python.exe pipeline.py ...
# Linux/mac:  .venv/bin/python pipeline.py ...
```
