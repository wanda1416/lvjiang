#!/usr/bin/env python3
"""用 scc 按项目边界统计主程序、测试与 Android 专属代码。

``src`` 是桌面端与 Android 共用的 Python 生产代码；Android 分组只统计
``android`` 下的平台专属代码，避免 Chaquopy 打包 ``src`` 后重复计数。

用法：
    python scripts/project_stats.py
    python scripts/project_stats.py --by-language
    python scripts/project_stats.py --top 20
    python scripts/project_stats.py --json
    python scripts/project_stats.py --scc /path/to/scc
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Group:
    key: str
    label: str
    paths: tuple[str, ...]


GROUPS = (
    Group("src", "主程序 src", ("src",)),
    Group("tests", "自动测试 tests", ("tests",)),
    Group(
        "android_app",
        "Android 应用",
        (
            "android/app/src/main",
            "android/build.gradle.kts",
            "android/settings.gradle.kts",
            "android/app/build.gradle.kts",
            "android/app/proguard-rules.pro",
        ),
    ),
    Group("android_stubs", "Android pystubs", ("android/app/pystubs",)),
)


@dataclass
class Counts:
    files: int = 0
    lines: int = 0
    code: int = 0
    comments: int = 0
    blanks: int = 0
    complexity: int = 0

    def __add__(self, other: "Counts") -> "Counts":
        return Counts(
            files=self.files + other.files,
            lines=self.lines + other.lines,
            code=self.code + other.code,
            comments=self.comments + other.comments,
            blanks=self.blanks + other.blanks,
            complexity=self.complexity + other.complexity,
        )


@dataclass
class Result:
    group: Group
    total: Counts
    languages: dict[str, Counts]
    files: list["SourceFile"]


@dataclass(frozen=True)
class SourceFile:
    path: str
    language: str
    counts: Counts


def _integer(record: dict, key: str) -> int:
    value = record.get(key, 0)
    return int(value) if isinstance(value, int | float) else 0


def _counts(record: dict, *, files_key: str = "Count") -> Counts:
    return Counts(
        files=_integer(record, files_key),
        lines=_integer(record, "Lines"),
        code=_integer(record, "Code"),
        comments=_integer(record, "Comment"),
        blanks=_integer(record, "Blank"),
        complexity=_integer(record, "Complexity"),
    )


def _normalise_path(value: str) -> str:
    path = value.replace("\\", "/")
    return path[2:] if path.startswith("./") else path


def parse_scc_json(raw: str) -> tuple[Counts, dict[str, Counts], list[SourceFile]]:
    """解析 scc 的语言汇总 JSON，兼容顶层数组和 ``languages`` 包装。"""
    payload = json.loads(raw)
    if isinstance(payload, dict):
        payload = payload.get("languages", payload.get("Languages", []))
    if not isinstance(payload, list):
        raise ValueError("scc JSON 顶层不是语言数组")

    languages: dict[str, Counts] = {}
    files: list[SourceFile] = []
    total = Counts()
    for record in payload:
        if not isinstance(record, dict):
            continue
        name = record.get("Name") or record.get("name")
        if not isinstance(name, str) or not name:
            continue
        counts = _counts(record)
        languages[name] = languages.get(name, Counts()) + counts
        total += counts
        raw_files = record.get("Files") or record.get("files") or []
        for file_record in raw_files:
            if not isinstance(file_record, dict):
                continue
            location = file_record.get("Location") or file_record.get("location")
            filename = file_record.get("Filename") or file_record.get("filename")
            path = location or filename
            if not isinstance(path, str) or not path:
                continue
            files.append(
                SourceFile(
                    _normalise_path(path),
                    str(file_record.get("Language") or name),
                    _counts(file_record, files_key="__file__"),
                )
            )
    return total, languages, files


def collect_group(scc: str, group: Group) -> Result:
    missing = [path for path in group.paths if not (ROOT / path).exists()]
    if missing:
        raise RuntimeError(f"{group.label} 配置了不存在的路径: {', '.join(missing)}")

    command = [scc, "--by-file", "--format", "json", *group.paths]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"scc 统计 {group.label} 失败: {detail}")
    try:
        total, languages, files = parse_scc_json(completed.stdout)
    except (ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"scc 返回了无法解析的 JSON（{group.label}）: {exc}") from exc
    return Result(group, total, languages, files)


def _sum(results: list[Result], *keys: str) -> Counts:
    wanted = set(keys)
    total = Counts()
    for result in results:
        if result.group.key in wanted:
            total += result.total
    return total


def _display_width(value: str) -> int:
    return sum(
        2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
        for char in value
    )


def _fit(value: str, width: int) -> str:
    """按终端显示宽度截断并右补空格，中文仍能对齐。"""
    if _display_width(value) <= width:
        return value + " " * (width - _display_width(value))
    suffix = "…"
    kept: list[str] = []
    used = _display_width(suffix)
    for char in value:
        size = 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
        if used + size > width:
            break
        kept.append(char)
        used += size
    return "".join(kept) + suffix + " " * (width - used)


def _number(value: int, width: int = 10) -> str:
    return f"{value:>{width},}"


def _stats_row(label: str, value: Counts, label_width: int = 22) -> str:
    return (
        f"{_fit(label, label_width)}"
        f"{_number(value.files, 7)}"
        f"{_number(value.lines)}"
        f"{_number(value.blanks)}"
        f"{_number(value.comments)}"
        f"{_number(value.code)}"
        f"{_number(value.complexity, 11)}"
    )


def _stats_header(label: str = "分组", label_width: int = 22) -> str:
    return (
        f"{_fit(label, label_width)}"
        f"{'文件':>7}{'总行数':>10}{'空白':>10}{'注释':>10}"
        f"{'代码':>10}{'复杂度':>11}"
    )


def print_table(results: list[Result], by_language: bool, top: int) -> None:
    android = _sum(results, "android_app", "android_stubs")
    production = _sum(results, "src", "android_app", "android_stubs")
    repository = _sum(results, *(group.key for group in GROUPS))

    border = "─" * 80
    print(border)
    print(_stats_header())
    print(border)
    for result in results:
        print(_stats_row(result.group.label, result.total))
    print(border)
    for label, value in (
        ("Android 合计", android),
        ("生产代码合计", production),
        ("含测试仓库合计", repository),
    ):
        print(_stats_row(label, value))
    print(border)

    if by_language:
        for result in results:
            print(f"\n{result.group.label} · 按语言")
            print(border)
            print(_stats_header("语言"))
            print(border)
            for name, value in sorted(
                result.languages.items(), key=lambda item: item[1].code, reverse=True
            ):
                print(_stats_row(name, value))
            print(border)

    if top > 0:
        print_top_files(results, top)


def print_top_files(results: list[Result], limit: int) -> None:
    files = sorted(
        (file for result in results for file in result.files),
        key=lambda file: (-file.counts.code, file.path),
    )[:limit]
    if not files:
        print("\n未从 scc JSON 中读到文件明细；请确认 scc 支持 --by-file。")
        return

    file_width = 72
    border = "─" * 120
    print(f"\nTop {len(files)} 文件 · 按代码行数")
    print(border)
    print(
        f"{'#':>3}  {_fit('文件', file_width)}"
        f"{_fit('语言', 14)}{'总行数':>9}{'空白':>8}{'注释':>8}"
        f"{'代码':>9}{'复杂度':>9}"
    )
    print(border)
    for index, file in enumerate(files, start=1):
        value = file.counts
        print(
            f"{index:>3}  {_fit(file.path, file_width)}"
            f"{_fit(file.language, 14)}"
            f"{_number(value.lines, 9)}{_number(value.blanks, 8)}"
            f"{_number(value.comments, 8)}{_number(value.code, 9)}"
            f"{_number(value.complexity, 9)}"
        )
    print(border)


def print_json(results: list[Result], scc: str, top: int) -> None:
    android = _sum(results, "android_app", "android_stubs")
    production = _sum(results, "src", "android_app", "android_stubs")
    repository = _sum(results, *(group.key for group in GROUPS))
    payload = {
        "tool": {"name": "scc", "executable": scc},
        "groups": {
            result.group.key: {
                "label": result.group.label,
                "paths": list(result.group.paths),
                "total": asdict(result.total),
                "languages": {
                    name: asdict(counts)
                    for name, counts in sorted(result.languages.items())
                },
            }
            for result in results
        },
        "derived": {
            "android": asdict(android),
            "production": asdict(production),
            "repository_with_tests": asdict(repository),
        },
        "top_files": [
            {
                "path": file.path,
                "language": file.language,
                **asdict(file.counts),
            }
            for file in sorted(
                (file for result in results for file in result.files),
                key=lambda file: (-file.counts.code, file.path),
            )[:top]
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scc", help="scc 可执行文件路径；默认从 PATH 查找")
    parser.add_argument(
        "--by-language", action="store_true", help="追加显示每个分组的语言明细"
    )
    parser.add_argument("--json", action="store_true", help="输出稳定的机器可读 JSON")
    parser.add_argument(
        "--top", type=int, default=10, metavar="N", help="显示代码行数最多的 N 个文件"
    )
    args = parser.parse_args(argv)

    scc = args.scc or shutil.which("scc")
    if not scc:
        print(
            "未找到 scc。请先安装 scc，或通过 --scc 指定可执行文件路径。\n"
            "项目地址：https://github.com/boyter/scc",
            file=sys.stderr,
        )
        return 2

    try:
        results = [collect_group(scc, group) for group in GROUPS]
    except RuntimeError as exc:
        print(f"统计失败：{exc}", file=sys.stderr)
        return 1

    if args.json:
        print_json(results, scc, max(0, args.top))
    else:
        print_table(results, args.by_language, max(0, args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
