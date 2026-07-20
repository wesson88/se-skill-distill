"""
阶段 1: Ingest — 调 skillMind 4 个采集器

输入: sources.yaml 中的 kind + url/path
输出: list[dict],每个 dict 含 source_hash / raw_path / title / doc_type
"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any

# 强制 Windows 控制台走 UTF-8,避免 GBK 编码 emoji 失败
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, io.UnsupportedOperation):
        pass

from _common import ensure_skillmind_venv, import_skillmind, SKILLMIND_ROOT


def ingest_one(source: dict, console=None) -> list[dict]:
    """
    调 skillMind 采集一个源。返回 list[dict](每个 dict 是 RawDocument 记录)。

    source 字段:
        kind: github_repo | github_raw | rss | single_url | local_dir
        url / path
        max_docs
    """
    ensure_skillmind_venv()
    collector, _, _ = import_skillmind()
    kind = source["kind"]
    results: list[dict] = []

    if kind in ("github_repo", "github_raw"):
        target = source["url"]
        results = collector.ingest_skill(target, console=console)
    elif kind == "rss":
        target = source["url"]
        max_items = int(source.get("max_docs", 50))
        results = collector.ingest_rss(target, console=console, max_items=max_items)
    elif kind == "single_url":
        target = source["url"]
        results = collector.ingest_url(target, console=console)
    elif kind == "local_dir":
        target = source["path"]
        # local_dir 走 ingest_skill 的本地模式
        results = collector.ingest_skill(target, console=console)
    else:
        raise ValueError(f"未知 kind: {kind}")

    return results


def ingest_batch(sources: list[dict], console=None) -> list[tuple[dict, list[dict]]]:
    """
    批量采集,每个 source 一组结果。
    返回 list[(source_dict, raw_docs)]
    """
    batch_results: list[tuple[dict, list[dict]]] = []
    for i, src in enumerate(sources, 1):
        if console:
            console.print(f"[cyan]({i}/{len(sources)}) 采集:[/cyan] {src.get('url') or src.get('path')}")
        try:
            docs = ingest_one(src, console=console)
            batch_results.append((src, docs))
            if console:
                new_count = sum(1 for d in docs if not d.get("skipped"))
                console.print(f"  [green]OK[/green] 新增 {new_count} / 跳过 {len(docs) - new_count}")
        except Exception as e:
            if console:
                console.print(f"  [bold red]FAIL[/bold red] 采集失败: {e}")
            batch_results.append((src, []))
    return batch_results


def main(argv: list[str] | None = None) -> int:
    """CLI 入口:从 --config 加载 sources,跑采集。"""
    import argparse
    import yaml
    from rich.console import Console

    parser = argparse.ArgumentParser(description="阶段 1: 采集 sources.yaml 中的源")
    parser.add_argument("--config", required=True, help="sources.yaml 路径")
    args = parser.parse_args(argv)

    console = Console()
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    sources = cfg.get("sources", [])

    console.print(f"[bold]阶段 1: 采集 {len(sources)} 个源[/bold]")
    results = ingest_batch(sources, console=console)

    total_new = sum(sum(1 for d in docs if not d.get("skipped")) for _, docs in results)
    total_skip = sum(len(docs) - sum(1 for d in docs if not d.get("skipped")) for _, docs in results)
    console.print(
        f"[bold green]完成[/bold green] 新增 {total_new} / 跳过 {total_skip} / 总源 {len(results)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
