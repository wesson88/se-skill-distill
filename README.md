# se-skill-distill 使用说明

> **基于 skillMind 的 SE 域知识蒸馏流水线**
> 从 SKILL.md / 博客 / 论坛等源抽取结构化知识 → 通过 audit 质量校验 → 路由到角色子目录 → 注入 Obsidian 风格 vault,顺带自动改角色基因 frontmatter。

---

## 安装(npm 全局,推荐)

```bash
npm install -g @wesson88/se-skill-distill
# postinstall 自动复制 skill 到 ~/.claude/skills/se-skill-distill/

# 使用前配置(全局安装后 vault 无法从 skill 位置自动推导):
export SE_VAULT=<你的 vault 根>            # 必需
# skillmind 前置:pip install skillmind,或 export SKILLMIND_REPO=<本地 skillMind 仓库>

# 验证(应输出 preflight 通过):
python ~/.claude/skills/se-skill-distill/scripts/preflight.py
```

skill 启动时(pipeline.py / distill_url.py)会自动 preflight 检查 vault + skillmind,不就绪会逐条给修复指引。

> 也可从源码跑:`python pipeline.py --config sources.yaml`(需在 skill 目录,Python 能 import skillmind)。

---

## 0. 怎么触发(30 秒上手)

```bash
# 1) 编辑 sources.yaml(列出要蒸馏的源)
# 2) 跑流水线
python pipeline.py --config sources.yaml
```

跑完后看 `_state/audit/approved.jsonl` 知道哪些源过了 audit(自动入了 vault),看 `_state/audit/manual-review.jsonl` 知道哪些需要人工 review。

---

## 1. 这是什么 / 不是

**是**:
- skillMind 的**编排器**(包了 ingest → extract → publish → audit → route → inject 6 步)
- 解决"抽取了但不知道质量如何"的问题
- 让 90% 以上覆盖率的抽取**自动入 vault**,不达标才卡人工

**不是**:
- skillMind 本身的替代品(还是依赖 skillMind 跑 extract)
- 全自动工具(audit FAIL 时仍需人)
- 跨平台开箱即用(代码是 Python,跨平台,但默认 vault 推导硬编码了 Windows 布局)

---

## 2. 怎么触发(完整方式)

### 2.1 触发方式

| 方式 | 适用 |
|---|---|
| **Claude Code skill 自动发现** | 把 `se-skill-distill` 放在 `.claude/skills/`,Claude Code 自动识别(`/skill` 列表里能看到) |
| **CLI 手动** | 直接调 `pipeline.py`,适合调试 / CI / batch |
| **CI / scheduled** | 调 CLI,exit code 0 = 全过;非 0 = 需 review |

### 2.2 CLI 完整参数

```bash
python pipeline.py --config <path> [选项]
```

| 参数 | 必填 | 说明 |
|---|---|---|
| `--config <path>` | ✅ | `sources.yaml` 路径 |
| `--vault <path>` | ❌ | vault 根路径(默认从脚本位置推导;也可 `SE_VAULT` env) |
| `--dry-run` | ❌ | 不写 vault,只跑流程 |
| `--skip-extract` | ❌ | 用 drafts/ 已存草稿,跳过 extract |
| `--skip-publish` | ❌ | 不 publish(已发布过) |
| `--status` | ❌ | 显示运行状态(ledger 概览) |

### 2.3 跨平台命令

| OS | 命令 |
|---|---|
| **Windows (PowerShell)** | `python pipeline.py --config sources.yaml` |
| **Linux / macOS (bash)** | `./.venv/bin/python pipeline.py --config sources.yaml` |

Windows 建议前置:
```powershell
$env:PYTHONIOENCODING = "utf-8"
chcp 65001 > $null
```

Linux/macOS:
```bash
export PYTHONIOENCODING=utf-8
```

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

`notes`: 仅自用,不影响流程。

---

## 4. vault 布局要求(必需)

```
{VAULT_ROOT}/
├── 00-系统/
│   ├── 角色基因/se/
│   │   ├── 角色-后端工程师.md   ← 被 patch 加 skill_refs
│   │   ├── 角色-前端工程师.md
│   │   ├── 角色-架构师.md
│   │   ├── 角色-技术主管.md
│   │   └── 角色-产品经理.md
│   └── 复盘记录/{date}-角色注入.md
└── 20-知识/
    └── 角色技能/se/
        ├── 后端工程师/B<n>-<title>.md   ← inject 写入
        ├── 前端工程师/
        ├── 架构师/
        ├── 技术主管/
        └── 产品经理/
```

`00-系统/角色基因/se/角色-<role>.md` 和 `20-知识/角色技能/se/` **都必需存在**。缺一个直接报错,提示用 `--vault`。

**vault 根路径解析顺序**(优先级高→低):
1. `--vault` CLI 参数
2. `SE_VAULT` 环境变量
3. 从脚本位置推导(`<vault>/.claude/skills/se-skill-distill/scripts` → 上 3 层 = `<vault>`)

---

## 5. 6 阶段流水线详解

| # | 阶段 | 做什么 | 跳过? |
|---|---|---|---|
| 1 | **ingest** | 调 `skillmind.ingest` 抓源到 `~/.skillmind/cache/raw/` | `--skip-extract`? NO(必要)|
| 2 | **extract** | 调 `skillmind.extractor.extract_skill` 抽草稿(LLM)到 `~/.skillmind/drafts/` | `--skip-extract` YES |
| 2.5 | **publish** | 调 `skillmind publish --all` 把 drafts 写进 vault | `--skip-publish` YES |
| 3 | **audit** | 调 `skillmind.auditor.audit_source` 算 coverage | NO(关键)|
| 4 | **route** | 按 role 选子目录,生成 `B<n>-<title>.md` 文件名 | NO(若 audit FAIL 则整体跳过)|
| 5 | **inject** | 写 skill 文件 + patch 角色基因 frontmatter + 落 ledger | NO |

**audit 判定**(stage 3 输出):

| coverage | verdict | needs_human_review | 后续 |
|---|---|---|---|
| ≥ 90% | `PASS` | `false` | 自动进 stage 4-5 |
| < 90% | `FAIL` | `true` | 停在该源,等人工 review `missing[]` |

---

## 6. 输出解读

跑完一次典型输出:

```
vault 根:<vault>
=== skillmind-to-vault 蒸馏流水线 ===
源数: 1

━━━ 阶段 3: audit (skillMind LLM, 90% 阈值) ━━━
  coverage=98.2%  verdict=PASS  needs_review=False  halluc=0  missing=0

  PASS=1  FAIL=0  / 总源 1

━━━ 阶段 4: route ━━━
  98.2%  后端工程师  B15-Brandkit Ima.md
  routed=1 unknown=0

━━━ 阶段 5: inject ━━━
  ✓ B15-Brandkit Ima.md → 后端工程师

=== 完成 ===  injected=1  failed=0
```

**落盘位置**:
- `_state/audit/approved.jsonl` — 自动通过的源
- `_state/audit/manual-review.jsonl` — FAIL,需人工
- `_state/audit/rejects.jsonl` — 硬错误(本版基本空)
- `_state/injection_ledger.jsonl` — 每次 inject 的原子记录
- `_state/routed.jsonl` — 路由结果
- `_state/unknown-queue.md` — role 无法判定的源

---

## 7. 失败处理流程

### 7.1 audit FAIL(coverage < 90%)

```bash
# 1. 看哪些源 FAIL
cat _state/audit/manual-review.jsonl

# 2. 看具体缺什么(missing[] 每条含 section/statement/reason)
cat _state/audit/manual-review.jsonl | python -m json.tool

# 3. 修 extract prompt 或补抽,然后清缓存重跑
rm ~/.skillmind/cache/extract_cache/<source_hash>_extract_v9.json
python pipeline.py --config sources.yaml
```

### 7.2 inject partial 失败

`04_inject.py` 出错时自动回滚(基于 `injection_ledger.jsonl` 记录)。无需手动干预。

### 7.3 角色无法判定

写入 `_state/unknown-queue.md`,跳过该条。手动在 sources.yaml 显式设 `target_role` 重跑。

---

## 8. 推荐工作流(批量)

```bash
# 1. 编辑 sources.yaml(加 5-10 个源)
# 2. 干跑看预演
python pipeline.py --config sources.yaml --dry-run

# 3. 正式跑
python pipeline.py --config sources.yaml

# 4. 看 audit 落盘,挑出需要修的
cat _state/audit/manual-review.jsonl

# 5. 修 extract prompt,清缓存,重跑
rm ~/.skillmind/cache/extract_cache/<hash>_extract_v9.json
python pipeline.py --config sources.yaml
```

---

## 9. 跨平台注意

| 项 | 状态 |
|---|---|
| Python 代码(`pathlib` 写法) | ✅ 跨平台 |
| skillMind 依赖 | ✅ 跨平台 |
| **默认 vault 推导** | ⚠️ 假设 `<vault>/.claude/skills/<x>/scripts/` 布局,Mac/Linux 上路径会变但仍正确 |
| **文档示例命令** | ⚠️ 当前是 PowerShell 风格,Mac/Linux 用户需用 bash |
| 路径分隔符 | ✅ `pathlib` 自动处理 `/` 和 `\` |
| ruamel.yaml | ✅ 三平台都有,`pip install ruamel.yaml` |

**Mac/Linux 用户建议**:
- 显式 `--vault <path>` 或 `export SE_VAULT=<path>`,不依赖推导
- 跑前 `export PYTHONIOENCODING=utf-8`(Python 3.7+ 默认 UTF-8,可不设)

---

## 10. FAQ

**Q: `skillmind` 命令找不到**
A: 用 `python -m skillmind` 代替,或加 `~/.local/bin`(pip 安装路径)到 PATH。

**Q: 报"VAULT_ROOT 无效"**
A: vault 必须同时有 `00-系统/` 和 `20-知识/` 子目录。用 `--vault <path>` 或 `SE_VAULT=<path>` 显式指定。

**Q: 报"找不到 source_hash"**
A: 源没在 `~/.skillmind/hashes.yaml`。先跑 `python -m skillmind ingest <url>` 一次,再跑 pipeline。

**Q: audit 报覆盖率 N%(N<90),missing 里写的 reason 看不懂**
A: 把 missing 列表给 LLM,问 "这个 reason 说的缺什么 extract 怎么改",或者直接 `python -m skillmind extract <source>` 重抽。

**Q: 想用不同模型做 audit(交叉验证)**
A: 在 `~/.skillmind/config.yaml` 设 `llm_profiles.audit.model`,如:
```yaml
llm_profiles:
  extract: {model: deepseek/deepseek-chat}
  audit:   {model: anthropic/claude-3-5-haiku}
```

**Q: inject 报"角色基因文件不存在"**
A: 缺 `00-系统/角色基因/se/角色-<role>.md`。创建该文件(参考 references/naming-rules.md)。

---

## 11. 相关文件

- `references/confidence-formula.md` — audit 判定规则(90% 阈值 + PASS/FAIL)
- `references/naming-rules.md` — `B<n>-<title>.md` 命名规则
- `references/rollback-runbook.md` — 失败回滚流程
- `references/source-schema.md` — sources.yaml schema
- `tests/test_e2e_smoke.py` — 端到端 smoke test
- `_state/audit/` — audit 落盘(PASS/FAIL/reject 分文件)
- `_state/injection_ledger.jsonl` — 注入原子记录(05_rollback 依据)

---

## 12. 完整命令速查

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
# Windows:    python pipeline.py ...
# Linux/mac:  .venv/bin/python pipeline.py ...
```
