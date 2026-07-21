"""共享常量与工具函数 — SE 域 skill 蒸馏流水线"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------

# skill 所在目录(用来推导默认 vault)
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent


def _derive_default_vault_root() -> Path:
    """从 SKILL_ROOT 向上推导 vault 根(se-skill-distill -> skills -> .claude -> adam)。"""
    return SKILL_ROOT.parent.parent.parent


def _resolve_vault_root(explicit: str | None = None) -> Path:
    """解析 vault 根路径,优先级:explicit > SE_VAULT 环境变量 > 推导。
    验证 vault 结构(00-系统/ + 20-知识/ 都存在)。
    """
    if explicit:
        v = Path(explicit).resolve()
    elif env := os.environ.get("SE_VAULT"):
        v = Path(env).resolve()
    else:
        v = _derive_default_vault_root()
    if not (v / "00-系统").exists() or not (v / "20-知识").exists():
        raise RuntimeError(
            f"VAULT_ROOT 无效:{v}\n"
            f"必须同时包含 00-系统/ 和 20-知识/ 子目录。\n"
            f"用 --vault <path> 或环境变量 SE_VAULT=<path> 指定"
        )
    return v


# 模块加载时先用默认路径初始化;set_vault_root() 可在 CLI 启动时覆盖
VAULT_ROOT: Path = _resolve_vault_root()
ROLE_GENE_DIR: Path = VAULT_ROOT / "00-系统" / "角色基因" / "se"
SKILL_DIR: Path = VAULT_ROOT / "20-知识" / "角色技能" / "se"
RETROSPECTIVE_DIR: Path = VAULT_ROOT / "00-系统" / "复盘记录"


def set_vault_root(explicit: str | None = None) -> Path:
    """重设 vault 根并刷新所有派生路径。CLI 参数或环境变量触发。
    必须在所有下游模块引用 VAULT_ROOT 之前调用。

    由于 Python import 时常量已被消费,这里用模块级 globals() 直接 mutate,
    调用方需自行确保引用方也动态查 _common.VAULT_ROOT 而不是用 from import 缓存。
    """
    global VAULT_ROOT, ROLE_GENE_DIR, SKILL_DIR, RETROSPECTIVE_DIR, ROLE_TO_GENE_FILE
    v = _resolve_vault_root(explicit)
    VAULT_ROOT = v
    ROLE_GENE_DIR = v / "00-系统" / "角色基因" / "se"
    SKILL_DIR = v / "20-知识" / "角色技能" / "se"
    RETROSPECTIVE_DIR = v / "00-系统" / "复盘记录"
    # 角色基因文件路径表:之前是模块加载时算的,set_vault_root 后要重算
    ROLE_TO_GENE_FILE.update({
        "后端工程师": ROLE_GENE_DIR / "角色-后端工程师.md",
        "前端工程师": ROLE_GENE_DIR / "角色-前端工程师.md",
        "架构师": ROLE_GENE_DIR / "角色-架构师.md",
        "技术主管": ROLE_GENE_DIR / "角色-技术主管.md",
        "产品经理": ROLE_GENE_DIR / "角色-产品经理.md",
    })
    return v

# skillMind 路径
SKILLMIND_ROOT = Path(os.environ.get("SKILLMIND_REPO", r"E:\workstation\ai\skillMind"))

# 状态目录(本 skill 内部)
STATE_DIR = SKILL_ROOT / "_state"
LEDGER_FILE = STATE_DIR / "injection_ledger.jsonl"
AUDIT_DIR = STATE_DIR / "audit"
APPROVED_FILE = AUDIT_DIR / "approved.jsonl"
REVIEW_FILE = AUDIT_DIR / "manual-review.jsonl"
REJECT_FILE = AUDIT_DIR / "rejects.jsonl"
UNKNOWN_FILE = STATE_DIR / "unknown-queue.md"

# 创建状态目录
for d in (STATE_DIR, AUDIT_DIR, RETROSPECTIVE_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# 角色映射
# ---------------------------------------------------------------------------

ROLE_TO_PREFIX = {
    "后端工程师": "B",
    "前端工程师": "F",
    "架构师": "A",
    "技术主管": "TL",
    "产品经理": "M",
}

ROLE_TO_SUBDIR = {
    "后端工程师": "后端工程师",
    "前端工程师": "前端工程师",
    "架构师": "架构师",
    "技术主管": "技术主管",
    "产品经理": "产品经理",
}

ROLE_TO_GENE_FILE = {
    "后端工程师": ROLE_GENE_DIR / "角色-后端工程师.md",
    "前端工程师": ROLE_GENE_DIR / "角色-前端工程师.md",
    "架构师": ROLE_GENE_DIR / "角色-架构师.md",
    "技术主管": ROLE_GENE_DIR / "角色-技术主管.md",
    "产品经理": ROLE_GENE_DIR / "角色-产品经理.md",
}

# 角色路由关键词(用于 auto 模式)
KEYWORD_RULES = {
    "前端工程师": [
        r"\bfetch\b", r"\bXHR\b", r"react", r"vue", r"\bcomponent\b",
        r"组件", r"渲染", r"\bDOM\b", r"\bCSS\b", r"webpack", r"vite",
    ],
    "后端工程师": [
        r"sqlite", r"fastapi", r"\bAPI\b", r"\bendpoint\b", r"\bORM\b",
        r"连接池", r"事务", r"查询", r"路由", r"\basync\b",
    ],
    "架构师": [
        r"架构", r"降级", r"依赖锁定", r"契约", r"\bDDD\b", r"微服务",
        r"system design", r"scalab", r"可用性", r"一致性",
    ],
    "技术主管": [
        r"任务分发", r"复盘", r"补丁", r"code review", r"团队",
        r"流程", r"规范", r"会议", r"站会",
    ],
    "产品经理": [
        r"\bPRD\b", r"用户故事", r"范围收敛", r"不做的事", r"产品定位",
        r"roadmap", r"stakeholder", r"市场", r"竞品",
    ],
}

# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class AuditResult:
    """审计产出。本层不再重算 score,直接读 skillMind audit_source() 的判定。
    字段对齐 skillMind 2.3+ 的 AuditReport(精简 JSON)。
    """
    source_hash: str
    draft_path: str
    role: str
    coverage: float = 0.0              # 0-100,直接读 skillMind.coverage_weighted
    hallucination_rate: float = 0.0     # 0-1,len(hallucinations) / len(items)
    needs_human_review: bool = False    # 直接读 skillMind.needs_human_review
    missing: list[dict] = field(default_factory=list)   # [{section, statement, reason}]
    verdict: str = "unknown"            # PASS / FAIL(直接读 skillMind.verdict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


# audit 入库阈值:单一来源在 skillmind.auditor.PASS_COVERAGE_THRESHOLD。
# 需要时 `import skillmind.auditor as a; a.PASS_COVERAGE_THRESHOLD`,本层不再维护本地副本。



@dataclass
class RoutedSkill:
    draft_path: str
    role: str
    prefix: str
    next_n: int
    new_filename: str  # "B8-空集守卫.md"
    new_path: Path  # 绝对路径
    target_subdir: Path


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

def atomic_write_text(path: Path, text: str) -> None:
    """原子写文本:tmp → fsync → os.replace。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """原子写二进制。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def append_ledger(record: dict) -> None:
    """追加一条 ledger 记录(JSONL 一行一条)。"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    record.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
    with LEDGER_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()


def read_ledger() -> list[dict]:
    if not LEDGER_FILE.exists():
        return []
    with LEDGER_FILE.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def safe_filename(title: str, max_len: int = 30) -> str:
    """
    将任意字符串转为安全文件名片段。
    - 去除控制字符
    - 替换 Windows 非法字符 \\ / : * ? " < > |
    - 折叠空白
    - 截断到 max_len
    """
    # 1. 去控制字符
    title = re.sub(r"[\x00-\x1f\x7f]", "", title)
    # 2. 替换非法字符
    title = re.sub(r'[\\/:*?"<>|]', "-", title)
    # 3. 折叠空白
    title = re.sub(r"\s+", " ", title).strip().strip("-").strip()
    # 4. 截断(按字符数,中文算 1)
    return title[:max_len] or "未命名"


def find_next_skill_number(subdir: Path, prefix: str) -> int:
    """在 subdir 下扫已有 {prefix}{N}-*.md 文件,返回 max(N) + 1。"""
    if not subdir.exists():
        return 1
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)-.*\.md$")
    max_n = 0
    for p in subdir.glob(f"{prefix}*-*.md"):
        m = pattern.match(p.name)
        if m:
            try:
                max_n = max(max_n, int(m.group(1)))
            except ValueError:
                continue
    return max_n + 1


def detect_role_from_text(text: str) -> str | None:
    """根据关键词在 text 中命中分数,返回最可能的角色。"""
    text_lower = text.lower()
    scores: dict[str, int] = {role: 0 for role in KEYWORD_RULES}
    for role, patterns in KEYWORD_RULES.items():
        for pat in patterns:
            if re.search(pat, text_lower, re.IGNORECASE):
                scores[role] += 1
    best_role = max(scores, key=lambda r: scores[r])
    return best_role if scores[best_role] > 0 else None


def ensure_skillmind_venv() -> None:
    """检查当前 Python 是否在 skillMind .venv 下,否则给出明确指引。"""
    exe = sys.executable
    expected = SKILLMIND_ROOT / ".venv"
    if not str(exe).startswith(str(expected)):
        print(
            f"[WARN] 当前 Python 不在 skillMind .venv 下: {exe}\n"
            f"      期望: {expected / 'Scripts' / 'python.exe' if os.name == 'nt' else 'bin/python'}\n"
            f"      请先激活 venv: {expected / 'Scripts' / 'activate.bat' if os.name == 'nt' else expected / 'bin' / 'activate'}"
        )
        # 不 abort — 让用户看到警告,自行决定


# ---------------------------------------------------------------------------
# SkillMind 集成
# ---------------------------------------------------------------------------

def import_skillmind():
    """动态 import skillMind,确保 sys.path 含 skillmind 包所在目录。"""
    pkg_root = SKILLMIND_ROOT
    if str(pkg_root) not in sys.path:
        sys.path.insert(0, str(pkg_root))
    try:
        from skillmind import collector, extractor, config  # noqa: F401
        return collector, extractor, config
    except ImportError as e:
        raise RuntimeError(
            f"无法 import skillMind,请检查:\n"
            f"  1. SKILLMIND_REPO={SKILLMIND_ROOT} 正确?\n"
            f"     (这是 skillMind 代码仓库路径,默认 <skillMind-repo>)\n"
            f"  2. 当前 Python 包含 skillmind 包? sys.executable={sys.executable}\n"
            f"原始错误: {e}"
        ) from e
