"""
Smoke Test: 端到端跑通 1 个 GitHub raw URL → 后端工程师 → 1 条新 skill → 角色基因 frontmatter 注入

跑法:
    cd <se-skill-distill 目录>
    python tests/test_e2e_smoke.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# 测试在 scripts/ 同级的 tests/ 下,但实际跑在仓库根
TEST_DIR = Path(__file__).resolve().parent
SKILL_ROOT = TEST_DIR.parent
VAULT_ROOT = Path(os.environ.get("SE_VAULT") or SKILL_ROOT.parent.parent.parent)  # SE_VAULT 优先,否则从脚本位置推


def run_step(cmd: list[str], desc: str) -> tuple[int, str, str]:
    """跑一个 shell 命令,返回 (exit_code, stdout, stderr)。"""
    print(f"\n>>> {desc}")
    print(f"    $ {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        cwd=str(SKILL_ROOT / "scripts"),
        capture_output=True,
        text=True,
        encoding="utf-8",
        shell=False,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(f"!!! {desc} failed with code {result.returncode}")
        print(result.stderr)
    return result.returncode, result.stdout, result.stderr


def main() -> int:
    # 1. snapshot 当前 角色-后端工程师.md
    gene_file = VAULT_ROOT / "00-系统" / "角色基因" / "se" / "角色-后端工程师.md"
    skill_dir = VAULT_ROOT / "20-知识" / "角色技能" / "se" / "后端工程师"
    snapshot_path = TEST_DIR / ".snapshot_gene.sha256"
    skill_snapshot = TEST_DIR / ".snapshot_skills.txt"

    print("=" * 70)
    print("Smoke Test: 1 source → 全链路")
    print("=" * 70)
    print(f"vault root: {VAULT_ROOT}")
    print(f"gene file:  {gene_file}")
    print(f"skill dir:  {skill_dir}")

    if not gene_file.exists():
        print(f"[FAIL] 角色基因文件不存在: {gene_file}")
        return 1

    import hashlib
    pre_sha = hashlib.sha256(gene_file.read_bytes()).hexdigest()
    pre_skills = sorted([p.name for p in skill_dir.glob("B*-*.md")]) if skill_dir.exists() else []
    snapshot_path.write_text(pre_sha, encoding="utf-8")
    skill_snapshot.write_text("\n".join(pre_skills), encoding="utf-8")
    print(f"\n[snapshot] gene sha256: {pre_sha}")
    print(f"[snapshot] 已有 B* skill: {len(pre_skills)} 条")

    # 2. 干跑(避免污染 vault)
    config_path = SKILL_ROOT / "sources.yaml"
    code, _, _ = run_step(
        [sys.executable, "pipeline.py", "--config", str(config_path), "--dry-run"],
        "干跑全链路(不写盘)",
    )
    if code != 0:
        print("[FAIL] 干跑失败")
        return 1

    # 3. 真实跑
    code, _, _ = run_step(
        [sys.executable, "pipeline.py", "--config", str(config_path)],
        "真跑全链路",
    )
    if code != 0 and code != 1:  # 1 表示 partial 失败但回滚了
        print(f"[FAIL] 真实跑退出码 {code}")
        return 1

    # 4. 验证
    print("\n" + "=" * 70)
    print("验证阶段")
    print("=" * 70)

    post_sha = hashlib.sha256(gene_file.read_bytes()).hexdigest()
    post_skills = sorted([p.name for p in skill_dir.glob("B*-*.md")]) if skill_dir.exists() else []
    new_skills = sorted(set(post_skills) - set(pre_skills))
    print(f"\n[verify] gene sha256 变化: {pre_sha != post_sha}")
    print(f"[verify] 新增 B* skill: {new_skills}")

    if not new_skills:
        print("[FAIL] 没有新 skill 文件被创建")
        return 1

    if pre_sha == post_sha:
        print("[FAIL] 角色基因 frontmatter 未变化")
        return 1

    # 5. 验证新 skill 文件 frontmatter 含 role + skill_id
    new_skill = skill_dir / new_skills[-1]
    text = new_skill.read_text(encoding="utf-8")
    if "role: 后端工程师" not in text:
        print(f"[FAIL] 新 skill frontmatter 缺 role: 后端工程师")
        return 1
    if f"skill_id: B" not in text:
        print(f"[FAIL] 新 skill frontmatter 缺 skill_id: B")
        return 1
    print(f"[verify] {new_skill.name} frontmatter 校验通过")

    # 6. 验证角色基因 frontmatter 含新 skill_ref
    gene_text = gene_file.read_text(encoding="utf-8")
    expected_ref = f"20-知识/角色技能/se/后端工程师/{new_skills[-1]}"
    if expected_ref not in gene_text:
        print(f"[FAIL] 角色基因未含 {expected_ref}")
        return 1
    print(f"[verify] 角色基因 frontmatter 含 {expected_ref}")

    # 7. rollback
    code, _, _ = run_step(
        [sys.executable, "05_rollback.py", "--list"],
        "列运行历史",
    )
    code, _, _ = run_step(
        [sys.executable, "05_rollback.py", "--dry-run"],
        "干跑回滚(只看 diff 不真改)",
    )
    # 真实回滚
    code, _, _ = run_step(
        [sys.executable, "05_rollback.py"],
        "真回滚",
    )

    # 8. 验证回滚
    final_sha = hashlib.sha256(gene_file.read_bytes()).hexdigest()
    final_skills = sorted([p.name for p in skill_dir.glob("B*-*.md")]) if skill_dir.exists() else []
    if final_sha != pre_sha:
        print(f"[FAIL] 回滚后 gene sha256 不一致")
        print(f"  pre:   {pre_sha}")
        print(f"  final: {final_sha}")
        return 1
    if final_skills != pre_skills:
        print(f"[FAIL] 回滚后 skill 列表不一致")
        print(f"  pre:   {pre_skills}")
        print(f"  final: {final_skills}")
        return 1

    # 9. 清理 .bak(rollback 应当已经删了)
    bak_file = gene_file.with_suffix(gene_file.suffix + ".bak")
    if bak_file.exists():
        print(f"[WARN] {bak_file} 仍存在,手动删除")
        bak_file.unlink()

    print("\n" + "=" * 70)
    print("[PASS] Smoke test 端到端通过")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
