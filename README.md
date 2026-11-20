# se-skill-distill

将 SkillMind 提炼的知识路由到角色技能目录，供引擎按技能自身的 trigger 动态召回。0.2.0 适配 SkillMind v2.5 的语义提炼、三态审计和哈希绑定证据；此仓负责编排和格式适配，提炼与语义判断由 SkillMind 完成。

## 运行准备

使用已安装 SkillMind v2.5 的 Python 环境。npm 分发只复制文件，不会安装 Python 依赖或激活虚拟环境。

```powershell
$env:SE_VAULT = '<vault 根目录>'
$env:SKILLMIND_REPO = '<SkillMind 源码目录>'  # 源码方式可设；已安装包可不设
$env:SKILLMIND_HOME = '<SkillMind 数据目录>'  # 未设时使用默认目录
$env:PYTHONIOENCODING = 'utf-8'
& "$env:SKILLMIND_REPO\.venv\Scripts\Activate.ps1"
python scripts/preflight.py
```

Linux/macOS 可激活同一安装环境的 `.venv/bin/activate`，环境变量含义相同。当前 Python 需具备 SkillMind 的依赖；正常入库不改写角色基因。

前置检查验证 vault 的 `00-系统/`、`20-知识/` 及 `semantic_distillation_v1`、四维审计和证据 API。能 import 旧版 SkillMind 并不代表兼容。`--vault <path>` 可替代 `SE_VAULT`，`--help` 不要求先配置 vault。

## 单个来源与批量运行

```bash
python scripts/distill_url.py https://github.com/obra/superpowers/blob/main/skills/writing-plans/SKILL.md --role 技术主管
python scripts/pipeline.py --config sources.yaml --vault <vault根目录>
python scripts/pipeline.py --config sources.yaml --skip-extract
python scripts/pipeline.py --config sources.yaml --dry-run
python scripts/pipeline.py --status
```

`--skip-extract` 复用当前来源的已存草稿，仍会审计。`--dry-run` 仍会采集、提取和审计，可能调用模型并写入缓存/隔离暂存；它不写最终角色技能或角色基因。没有离线回放数据时不要把这两个选项当作免费运行。

GitHub `SKILL.md` / `*.skill.md` 的 blob、raw 链接走 Skill 采集，保留指定 revision；raw 会归一化为 SkillMind v2.5 支持的 blob 下载入口。普通 README、博客仍按文档类型处理，不会自动伪装成 Skill。来源配置见 [source-schema.md](references/source-schema.md)。

## 流程与准入

采集 → 提取 → 当前批次隔离发布 → 审计 → 角色路由 → 技能及证据入库。

- 仅发布本次请求来源的草稿到独立审计暂存目录，不运行全局 `publish --all`，不依赖 PATH 中另一个 `skillmind`。
- 审计绑定全部来源草稿、暂存产物及报告中的文件集合。缺卡、证据不符、文件被改写或审计不完整均不能放行；多卡片来源全部保留。
- `PASS` 且无需人工复核才有入库资格；新语义 Skill 还必须具有覆盖、来源支持、知识重组、原文台账四维 PASS。覆盖百分比只展示，不在本层计算放行阈值。
- `FAIL` 与 `UNKNOWN` 均保存到人工复核队列。覆盖率允许为未测；满覆盖仍可能因杜撰或缺乏提炼而 FAIL。具体原因、四维状态与详细报告保留。
- 入库前重新核对草稿与审计快照。最终正文保留 `distillation.sections` 的标题与内容，外层只添加角色 frontmatter 和引用，不再次提炼或拆回旧槽位。
- 最终 Markdown 与邻接 `.evidence.json` 绑定新的实际文件哈希；不能复用暂存文件的旧哈希。证据随入库、回滚处理，保留外部编辑或非本工具拥有的文件。
- 遵守当前 vault 角色基因规范 §10：技能目录与 `trigger` 是唯一召回依据，不新增 `skill_refs` / `rule_refs`、不改写或创建角色基因。触发词取自已审元信息；按 §10.4 至少有一个在本角色目录独有的关键词，重复冲突进入复核，不编造新词或用角色名兜底。更新自己的文件不算关键词冲突。

普通非 Skill 文档保留旧正文渲染，由当前 SkillMind 返回其审计 verdict。详细契约见 [审计说明](references/confidence-formula.md)。

## 状态、输出与失败

默认状态位于本仓 `_state/`，可用 `SE_STATE_DIR` 改到本机私有目录。SkillMind 数据位置由其配置/API 决定，审计和草稿查找不会硬编码另一个 home。

| 位置 | 内容 |
|---|---|
| `_state/audit/approved.jsonl` | 完整且批准的审计记录、草稿与暂存哈希 |
| `_state/audit/manual-review.jsonl` | FAIL、UNKNOWN、来源/提取/证据/入库异常 |
| `_state/runs/` | 主流水线每轮独立的审计产物 |
| `_state/routed.jsonl` | 路由结果，携带审计快照供注入验证 |
| `_state/injection_ledger.jsonl` | 正文与证据事务的写入/回滚记录 |
| `<vault>/20-知识/角色技能/<domain>/<role>/` | 最终角色技能与证据 |

只要有来源未成功通过或入库，流水线就返回非零；不能把“没有 PASS”当作成功。关键词无法确定角色时需人工指定 `--role` / `target_role`。角色目录使用既有前缀与编号，参见 [命名规则](references/naming-rules.md)。

回滚命令与边界见 [rollback-runbook.md](references/rollback-runbook.md)。修改已生成的 Markdown 会使证据失效，审阅后应从规范草稿重新生成。

## 本地验证

```bash
python -m pytest tests -q
```

回归使用隔离 vault、数据和状态，模型边界替换为离线响应，不需要密钥。历史手动端到端脚本 `tests/test_e2e_smoke.py` 的 main 会运行配置来源并写 vault，不属于离线 pytest 回归；不要将它当作无副作用检查。
