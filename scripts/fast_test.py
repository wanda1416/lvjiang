"""按变更文件只跑相关测试 —— 迭代期间替代全量 pytest

用法:
    # 自动检测 git未提交变更，映射到相关测试文件
    python scripts/fast_test.py

    # 指定源文件（支持多个）
    python scripts/fast_test.py src/lvjiang/apps/yysls/ui/tuning_tab.py

    # 指定测试目录或文件（直接透传给 pytest）
    python scripts/fast_test.py tests/yysls/test_auto_tuning_flow.py

    # 全量（等同 pytest tests/）
    python scripts/fast_test.py --all

映射规则:
    源文件名 xxx.py → 查找 test_xxx.py
    找不到精确匹配时，按源文件所在目录映射到 tests/ 对应子目录
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
TEST_DIR = ROOT / "tests"

# 手动补充：源文件 stem → 测试文件名（命名不一致时在此补映射）
MANUAL_MAP: dict[str, list[str]] = {
    "auto_tuning": ["test_auto_tuning_flow.py"],
    "tuning_progress_hub": ["test_tuning_tab.py"],
    "tuning_progress_dialog": ["test_tuning_tab.py"],
    "cell_formatting": ["test_profile_db.py", "test_profile_overview_regen_normalize.py"],
}


def _find_tests_for_file(filepath: Path) -> list[str]:
    """单个源文件 → 相关测试文件列表"""
    rel = filepath.resolve().relative_to(ROOT)
    stem = filepath.stem

    # 1. 手动映射
    if stem in MANUAL_MAP:
        return [str(TEST_DIR / "yysls" / f) for f in MANUAL_MAP[stem]]

    # 2. 按 stem 全局搜索 test_{stem}.py
    matches = list(TEST_DIR.rglob(f"test_{stem}.py"))
    if matches:
        return [str(m) for m in matches]

    # 3. 按目录映射：src/lvjiang/X/... → tests/X/
    parts = rel.parts
    if parts[0] == "src" and len(parts) > 1:
        # src/lvjiang/apps/yysls/ui/xxx.py → tests/yysls/
        # src/lvjiang/core/xxx.py → tests/core/
        # src/lvjiang/ui/xxx.py → tests/ui/
        # src/lvjiang/workflows/xxx.py → tests/workflows/
        for segment in parts:
            candidate = TEST_DIR / segment
            if candidate.is_dir():
                return [str(candidate)]

    return []


def _get_changed_files() -> list[str]:
    """从 git 获取未提交的变更文件"""
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        capture_output=True, text=True, cwd=ROOT,
    )
    files = [f.strip() for f in result.stdout.splitlines() if f.strip()]
    # 也包含未跟踪的新文件
    result2 = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        capture_output=True, text=True, cwd=ROOT,
    )
    files.extend(f.strip() for f in result2.stdout.splitlines() if f.strip())
    return files


def _resolve_tests(targets: list[str]) -> list[str]:
    """将目标文件列表解析为测试文件列表"""
    test_files: set[str] = set()
    for t in targets:
        p = Path(t)
        if not p.is_absolute():
            p = ROOT / p
        # 已经是测试文件 → 直接用
        if p.exists() and p.name.startswith("test_") and p.suffix == ".py":
            test_files.add(str(p))
        # 是测试目录 → 整个目录
        elif p.is_dir() and str(p.relative_to(ROOT)).startswith("tests"):
            test_files.add(str(p))
        # 源文件 → 映射
        elif str(p).endswith(".py"):
            found = _find_tests_for_file(p)
            test_files.update(found)
    return sorted(test_files)


def main():
    parser = argparse.ArgumentParser(description="按变更文件跑相关测试")
    parser.add_argument("files", nargs="*", help="源文件或测试文件路径")
    parser.add_argument("--all", action="store_true", help="跑全量测试")
    parser.add_argument("--dry", action="store_true", help="只打印测试文件，不执行")
    parser.add_argument("-k", type=str, default="", help="pytest -k 关键字过滤")
    args = parser.parse_args()

    if args.all:
        cmd = [sys.executable, "-m", "pytest", "tests/", "-x", "-q"]
        if args.k:
            cmd.extend(["-k", args.k])
        print(f"▶ 全量测试: {' '.join(cmd)}")
        if not args.dry:
            subprocess.run(cmd, cwd=ROOT)
        return

    # 确定变更文件
    if args.files:
        targets = args.files
    else:
        targets = _get_changed_files()
        if not targets:
            print("[ok] 无未提交变更，无需运行测试")
            return
        print(f"[info] 检测到变更文件 ({len(targets)}):")
        for f in targets:
            print(f"   {f}")
        print()

    # 映射到测试
    test_files = _resolve_tests(targets)
    if not test_files:
        print("[warn] 未找到相关测试文件，回退到全量测试")
        test_files = ["tests/"]

    print(f"[test] 将运行 {len(test_files)} 个测试目标:")
    for t in test_files:
        print(f"   {t}")

    cmd = [sys.executable, "-m", "pytest", *test_files, "-x", "-q"]
    if args.k:
        cmd.extend(["-k", args.k])
    print(f"\n>> {' '.join(cmd)}")
    if not args.dry:
        subprocess.run(cmd, cwd=ROOT)


if __name__ == "__main__":
    main()
