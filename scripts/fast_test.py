"""开发期快速验证入口：受影响测试、精确用例、失败重跑与全量验证。

默认使用 pytest-testmon 根据历史覆盖关系，只选择受当前代码改动影响的测试。
第一次运行会执行全量测试并建立 ``.testmondata``，后续才能实现行级筛选。

常用命令：
    python scripts/fast_test.py
    python scripts/fast_test.py tests/core/test_config_resolver.py::TestModeDetection::test_env_forces_dev
    python scripts/fast_test.py -k config_resolver
    python scripts/fast_test.py --lf
    python scripts/fast_test.py --all
    python scripts/fast_test.py --dry -- -vv --tb=short

这是一条开发反馈环，不代替提交前的 ``--all`` 或 CI。外部配置、资源文件、
原生平台行为等覆盖率无法追踪的变化，仍应显式指定测试目标。
"""
from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEST_DIR = ROOT / "tests"

# 兼容旧用法：显式传入源文件时，处理测试文件与源文件命名不一致的情况。
MANUAL_MAP: dict[str, list[str]] = {
    "auto_tuning": ["test_auto_tuning_flow.py"],
    "tuning_progress_hub": ["test_tuning_tab.py"],
    "tuning_progress_widget": ["test_tuning_tab.py"],
    "cell_formatting": [
        "test_profile_db.py",
        "test_profile_overview_regen_normalize.py",
    ],
}


def _find_tests_for_source(filepath: Path) -> list[str]:
    """将显式指定的源文件映射到已有测试，供兼容模式使用。"""
    stem = filepath.stem
    if stem in MANUAL_MAP:
        return [str(TEST_DIR / "yysls" / name) for name in MANUAL_MAP[stem]]

    matches = sorted(TEST_DIR.rglob(f"test_{stem}.py"))
    if matches:
        return [str(path) for path in matches]

    try:
        rel = filepath.resolve().relative_to(ROOT)
    except ValueError:
        return []
    if rel.parts[:1] == ("src",):
        for segment in rel.parts[1:]:
            candidate = TEST_DIR / segment
            if candidate.is_dir():
                return [str(candidate)]
    return []


def _resolve_targets(targets: list[str]) -> list[str]:
    """保留 pytest node id；仅对显式源文件沿用旧映射规则。"""
    resolved: set[str] = set()
    for target in targets:
        path_text, separator, node_id = target.partition("::")
        path = Path(path_text)
        absolute = path if path.is_absolute() else ROOT / path

        if separator or path_text.startswith("tests/") or absolute == TEST_DIR:
            resolved.add(target)
        elif absolute.suffix == ".py" and "src" in absolute.parts:
            resolved.update(_find_tests_for_source(absolute))
        else:
            # 允许 pytest 自己解释包名、目录或其他 selector，并给出标准错误。
            resolved.add(target)
    return sorted(resolved)


def _changed_python_files() -> list[str]:
    """返回已跟踪改动与未跟踪的 Python 文件，供 Ruff 快速检查。"""
    commands = [
        ["git", "diff", "--name-only", "--diff-filter=ACMR", "HEAD"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    ]
    files: set[str] = set()
    for command in commands:
        result = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            continue
        files.update(
            name
            for name in result.stdout.splitlines()
            if name.endswith(".py") and (ROOT / name).is_file()
        )
    return sorted(files)


def _format_command(command: list[str]) -> str:
    """使用 Python 自带规则显示可复制的跨平台命令。"""
    return subprocess.list2cmdline(command)


def _run(command: list[str], *, dry: bool) -> int:
    print(f">> {_format_command(command)}")
    if dry:
        return 0
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def _pytest_command(selectors: list[str], extra: list[str]) -> list[str]:
    return [sys.executable, "-m", "pytest", *selectors, "-x", "-q", *extra]


def _parse_args(argv: list[str] | None) -> tuple[argparse.Namespace, list[str]]:
    raw = list(sys.argv[1:] if argv is None else argv)
    if "--" in raw:
        separator = raw.index("--")
        own_args, passthrough = raw[:separator], raw[separator + 1 :]
    else:
        own_args, passthrough = raw, []

    parser = argparse.ArgumentParser(
        description="快速运行受代码行改动影响的测试（首次运行建立全量基线）"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--all", action="store_true", help="运行全量测试并刷新影响数据库")
    mode.add_argument("--lf", action="store_true", help="只重跑上次失败的测试")
    parser.add_argument("targets", nargs="*", help="测试 node id、测试路径或源文件")
    parser.add_argument("-k", metavar="EXPR", help="按 pytest 关键字表达式筛选")
    parser.add_argument("--dry", action="store_true", help="只显示命令，不执行")
    parser.add_argument("--no-lint", action="store_true", help="跳过变更 Python 文件的 Ruff 检查")
    args, unknown = parser.parse_known_args(own_args)
    return args, [*unknown, *passthrough]


def main(argv: list[str] | None = None) -> int:
    args, extra = _parse_args(argv)

    if not args.no_lint:
        changed = _changed_python_files()
        if changed:
            print(f"[lint] Ruff 检查 {len(changed)} 个变更 Python 文件")
            lint_code = _run(
                [sys.executable, "-m", "ruff", "check", *changed], dry=args.dry
            )
            if lint_code:
                return lint_code

    pytest_extra = list(extra)
    if args.k:
        pytest_extra.extend(["-k", args.k])

    if args.lf:
        print("[test] 重跑上次失败的测试")
        return _run(
            _pytest_command(["tests/", "--lf", "--lfnf=none"], pytest_extra),
            dry=args.dry,
        )

    if args.all:
        print("[test] 全量测试，同时刷新 testmon 影响数据库")
        return _run(
            _pytest_command(
                ["tests/", "--testmon", "--testmon-noselect"], pytest_extra
            ),
            dry=args.dry,
        )

    if args.targets or args.k:
        selectors = _resolve_targets(args.targets) or ["tests/"]
        print(f"[test] 精确选择 {len(selectors)} 个测试目标")
        return _run(_pytest_command(selectors, pytest_extra), dry=args.dry)

    if importlib.util.find_spec("testmon") is None:
        print(
            "[error] 缺少 pytest-testmon；请先安装开发依赖：pip install -e '.[dev]'",
            file=sys.stderr,
        )
        return 2

    print("[test] 运行受当前代码行改动影响的测试（首次使用会运行全量测试）")
    return _run(
        _pytest_command(["tests/", "--testmon"], pytest_extra), dry=args.dry
    )


if __name__ == "__main__":
    raise SystemExit(main())
