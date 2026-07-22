"""
distill_url.py — 一键蒸馏:给一个 GitHub skill URL,跑全流程(ingest → extract → audit → route → inject)。

跳过 sources.yaml 配置,直接 URL → ingest → 临时 sources → pipeline → 还原。

支持 URL 形式:
  - https://github.com/owner/repo/blob/main/path/SKILL.md  →  raw URL
  - https://github.com/owner/repo/tree/main/skills         →  github_repo + subdir
  - https://raw.githubusercontent.com/owner/repo/main/path/SKILL.md  →  raw URL 直传
  - owner/repo                                                →  github_repo(全仓扫 SKILL.md)
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
SE_ROOT = SCRIPT_DIR.parent
SOURCES_YAML = SE_ROOT / "sources.yaml"
PIPELINE = SCRIPT_DIR / "pipeline.py"


def resolve_url(url: str) -> list[dict]:
    """
    把任意 GitHub URL 形式转 sources.yaml 入口列表。
    返回: list of {"kind": ..., "url/path": ..., "target_role": ..., "notes": ...}
    """
    notes = f"distill_url 一次性蒸馏:{url}"
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()

    # 形式 1: 短形式 "owner/repo"
    if re.match(r"^[\w.-]+/[\w.-]+$", url.strip("/")):
        owner, repo = url.strip("/").split("/", 1)
        return [{
            "kind": "github_repo",
            "url": f"https://github.com/{owner}/{repo}",
            "target_role": "auto",
            "notes": notes,
        }]

    # 形式 2: 已经是 raw
    if host == "raw.githubusercontent.com":
        return [{"kind": "single_url", "url": url, "target_role": "auto", "notes": notes}]

    # 形式 3: GitHub blob URL
    if host in ("github.com", "www.github.com"):
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2:
            owner, repo = parts[0], parts[1]
            # /blob/<branch>/<path>
            if len(parts) >= 5 and parts[2] == "blob":
                branch = parts[3]
                file_path = "/".join(parts[4:])
                raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{file_path}"
                return [{"kind": "single_url", "url": raw_url, "target_role": "auto", "notes": notes}]
            # /tree/<branch>/<subdir>
            if len(parts) >= 4 and parts[2] == "tree":
                branch = parts[3]
                subdir = "/".join(parts[4:]) if len(parts) > 4 else ""
                tree_url = f"https://github.com/{owner}/{repo}/tree/{branch}/{subdir}".rstrip("/")
                return [{
                    "kind": "github_repo",
                    "url": tree_url,
                    "target_role": "auto",
                    "notes": notes,
                }]
            # /owner/repo 默认 main 分支
            return [{
                "kind": "github_repo",
                "url": f"https://github.com/{owner}/{repo}",
                "target_role": "auto",
                "notes": notes,
            }]

    # 形式 4: 不认识的形式,直接当 single_url
    return [{"kind": "single_url", "url": url, "target_role": "auto", "notes": notes}]


def render_sources_yaml(entries: list[dict]) -> str:
    """把入口列表渲染成 sources.yaml 文本。"""
    lines = ["sources:"]
    for e in entries:
        lines.append(f"  - kind: {e['kind']}")
        lines.append(f"    url: {e['url']}")
        if e.get("path"):
            lines.append(f"    path: {e['path']}")
        lines.append(f"    target_role: {e['target_role']}")
        if e.get("notes"):
            notes = e["notes"].replace("'", "''")  # YAML 单引号转义
            lines.append(f"    notes: '{notes}'")
    return "\n".join(lines) + "\n"


def find_venv_python() -> str:
    """返回当前 python 解释器。

    preflight 已保证 sys.executable 能 import skillmind,直接用它。不再
    硬编码/推导 skillMind venv 路径 —— npm 全局安装后 skill 位置与 skillMind
    仓库无父子关系,旧的 SE_ROOT.parent.parent 推导和硬编码 E:/workstation 路径都会失效。
    """
    return sys.executable


def main() -> int:
    import preflight; preflight.check()  # 前置检查 vault + skillmind
    parser = argparse.ArgumentParser(
        description="一键蒸馏:给一个 GitHub skill URL,跑 ingest → extract → audit → route → inject 全流程"
    )
    parser.add_argument("url", help="GitHub URL(blob/tree/raw/owner-repo)或 owner/repo")
    parser.add_argument("--role", default="后端工程师", help="目标角色(默认后端工程师)")
    parser.add_argument("--vault", help="vault 根路径(默认从脚本位置推导)")
    parser.add_argument("--domain", default="se", help="角色域(默认 se;agent-workflow 当前只扫 se)")
    parser.add_argument("--skip-extract", action="store_true", help="跳过 extract(用已有草稿)")
    parser.add_argument("--dry-run", action="store_true", help="干跑,不写 vault")
    parser.add_argument("--yes", "-y", action="store_true", help="跳过确认,直接跑")
    args = parser.parse_args()

    entries = resolve_url(args.url)
    for e in entries:
        e["target_role"] = args.role

    print(f"输入: {args.url}")
    print(f"解析 → {len(entries)} 个 source 入口:")
    for e in entries:
        print(f"  - {e['kind']}: {e['url']}")
    print(f"  target_role: {args.role}")
    print()

    if not args.yes:
        try:
            input("回车继续(或 Ctrl+C 取消):")
        except EOFError:
            pass

    # 备份原 sources.yaml(字节级,避免 read_text/write_text 在 Windows 转换行尾 LF→CRLF 污染 git)
    backup = SOURCES_YAML.read_bytes() if SOURCES_YAML.exists() else None

    # 写临时 sources.yaml
    SOURCES_YAML.parent.mkdir(parents=True, exist_ok=True)
    SOURCES_YAML.write_bytes(render_sources_yaml(entries).encode("utf-8"))

    try:
        # 跑 pipeline
        venv_py = find_venv_python()
        cmd = [venv_py, str(PIPELINE), "--config", str(SOURCES_YAML)]
        if args.vault:
            cmd.extend(["--vault", args.vault])
        if args.domain and args.domain != "se":
            cmd.extend(["--domain", args.domain])
        if args.skip_extract:
            cmd.append("--skip-extract")
        if args.dry_run:
            cmd.append("--dry-run")
        print(f"$ {' '.join(cmd)}\n")
        return subprocess.run(cmd).returncode
    finally:
        # 还原 sources.yaml
        if backup is not None:
            SOURCES_YAML.write_bytes(backup)
        else:
            SOURCES_YAML.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
