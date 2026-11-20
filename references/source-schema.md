# sources.yaml 字段约定

## 完整 schema

```yaml
sources:
  - kind: <enum>          # 必填,见下表
    url: <string>         # 必填(kind ∈ {github_repo, github_raw, rss, single_url})
    path: <string>        # 必填(kind = local_dir)
    target_role: <enum>   # 必填,见下表
    max_docs: <int>       # 可选，RSS 限流；未设继承现有采集器默认 50
    notes: <string>       # 可选,仅团队沟通用
```

## kind 字段

| 值 | 行为 | 调用 skillMind 函数 |
|---|---|---|
| `github_repo` | clone 仓库(深度 1)→ 递归扫 SKILL.md/CLAUDE.md/AGENTS.md 等 | `ingest_skill(url)` |
| `github_raw` | 单文件直下载(GitHub raw URL),不克隆 | `ingest_skill(url)` (内部检测到 blob URL 自动转 raw) |
| `rss` | 解析 RSS/Atom Feed → 抓每条 → 缓存 | `ingest_rss(url, max_items=max_docs)` |
| `single_url` | 普通文章/README 按文档采集；GitHub Skill 文件识别后走 Skill | `ingest_url(url)` 或 `ingest_skill(blob_url)` |
| `local_dir` | 本地目录 → 递归扫 SKILL.md 等 | `ingest_skill(path)` (本地模式) |

**注**：`github_raw` 与 `github_repo` 实际都调 `ingest_skill`。单文件 raw URL 先归一化为 GitHub blob URL，才能触发 SkillMind v2.5 的直接下载路径；revision 保持不变。自动识别范围封闭为 GitHub 的 `SKILL.md` / `*.skill.md` 文件名。普通 README 应使用 `single_url`。

## target_role 字段

| 值 | 路由结果 |
|---|---|
| `后端工程师` | B* 前缀,后端子目录,改 `角色-后端工程师.md` |
| `前端工程师` | F* 前缀,前端子目录,改 `角色-前端工程师.md` |
| `架构师` | A* 前缀,架构子目录,改 `角色-架构师.md` |
| `技术主管` | TL* 前缀,技术主管子目录,改 `角色-技术主管.md` |
| `产品经理` | M* 前缀,产品经理子目录(待建),改 `角色-产品经理.md` |
| `auto` | 走关键词自动路由(见 naming-rules.md) |

## URL 示例

```yaml
# 1. GitHub raw 文件(单文件,推荐 smoke test)
- kind: github_raw
  url: "https://raw.githubusercontent.com/obra/superpowers/main/skills/writing-plans/SKILL.md"

# 2. GitHub 仓库(整库扫 SKILL.md)
- kind: github_repo
  url: https://github.com/anthropics/skills

# 3. GitHub 仓库子目录
- kind: github_repo
  url: https://github.com/tiangolo/full-stack-fastapi-template/tree/master/backend

# 4. RSS feed
- kind: rss
  url: https://web.dev/feed.xml
  max_docs: 5

# 5. 单篇文章
- kind: single_url
  url: https://martinfowler.com/articles/microservices.html

# 6. 本地目录
- kind: local_dir
  path: ~/workstation/ai/skillMind
```

## 校验

加载时做以下校验(在 `pipeline.py` 启动时):

1. 必填字段非空
2. kind 是 enum 之一
3. kind ∈ {github_*, rss, single_url} → url 非空
4. kind = local_dir → path 非空
5. target_role 是 enum 之一
6. URL scheme 是 http/https(本地除外)

失败时抛 `ValueError` + 给出模板,引导用户修正。
