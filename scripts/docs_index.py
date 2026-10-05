"""生成并校验按目录归档的文档索引（开发日志按月、发布说明按版本）。

用法：

    python scripts/docs_index.py            # 补齐索引里漏登记的行
    python scripts/docs_index.py --check    # 只校验，漏登记 / 死登记时退出码 1

索引表的人工列（开发日志的「主题」、发布说明的「说明」）只在**新增行**时按
一级标题自动填写；已有行一律原样保留，不覆盖手写措辞。
"""

from __future__ import annotations

import argparse
import pathlib
import re
from collections.abc import Callable

REPO = pathlib.Path(__file__).resolve().parent.parent
DEVLOG_ROOT = REPO / "docs" / "40-development"
RELEASES = REPO / "docs" / "50-releases"
MONTHLY_DIRS = ("2026-07", "2026-08", "2026-09")

H1 = re.compile(r"^#\s+(.*)$", re.M)
DATE_IN_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})-")
PUBLISHED = re.compile(r"发布日期[:：]\s*(\d{4}-\d{2}-\d{2})")
DEVLOG_HEADER = "| 日期（日志日） | 文件 | 主题 |"
RELEASE_HEADER = "| 版本 | 发布日期 | 说明 |"
ROW_DATE = re.compile(r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|")
ROW_VERSION = re.compile(r"^\|\s*\[(v?[\d.]+)")
LINK_TARGET = re.compile(r"\]\(([^)]+)\)")


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def title_of(path: pathlib.Path) -> str:
    match = H1.search(read(path))
    title = match.group(1).strip() if match else path.stem
    return re.sub(r"^(Dev Log[:：]|v[\d.]+)\s*[—\-:：]*\s*", "", title).strip()


def devlog_row(path: pathlib.Path) -> str:
    match = DATE_IN_NAME.match(path.name)
    date = match.group(1) if match else "0000-00-00"
    return f"| {date} | [{path.name}]({path.name}) | {title_of(path)} |"


def release_row(path: pathlib.Path) -> str:
    published = PUBLISHED.search(read(path))
    date = published.group(1) if published else "—"
    return f"| [{path.stem}]({path.name}) | {date} | {title_of(path)} |"


def devlog_key(row: str) -> str:
    match = ROW_DATE.match(row)
    return match.group(1) if match else "0000-00-00"


def release_key(row: str) -> str:
    match = ROW_VERSION.match(row)
    if not match:
        return "000000000"
    parts = [int(part) for part in re.findall(r"\d+", match.group(1))[:4]]
    return ".".join(f"{part:04d}" for part in parts)


def sort_rows(rows: list[str], key_of) -> list[str]:
    return sorted(rows, key=key_of, reverse=True)


def collect(directory: pathlib.Path, index: pathlib.Path, key_of
            ) -> tuple[list[str], list[str]]:
    text = read(index)
    existing = [line for line in text.splitlines()
                if line.startswith("|") and (ROW_DATE.match(line) or ROW_VERSION.match(line))]
    listed = {match.group(1) for row in existing
              if (match := LINK_TARGET.search(row))}
    problems: list[str] = []
    added: list[str] = []
    builder = devlog_row if directory != RELEASES else release_row
    for path in sorted(directory.glob("*.md")):
        if path.name == "README.md" or path.name in listed:
            continue
        added.append(builder(path))
        problems.append(f"漏登记 {index.parent.name}/{index.name} -> {path.name}")
    for target in listed:
        if not (directory / target).is_file():
            problems.append(f"死登记 {index.parent.name}/{index.name} -> {target}")
    return sort_rows(existing + added, key_of), problems


def rewrite(index: pathlib.Path, rows: list[str], key_of) -> None:
    out: list[str] = []
    inserted = False
    for line in read(index).splitlines():
        if line.startswith("|") and (ROW_DATE.match(line) or ROW_VERSION.match(line)):
            if not inserted:
                out.extend(rows)
                inserted = True
            continue
        out.append(line)
    index.write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="归档文档索引")
    parser.add_argument("--check", action="store_true", help="只校验不写入")
    args = parser.parse_args()

    targets: list[tuple[pathlib.Path, Callable[[str], str]]] = [
        (DEVLOG_ROOT / month, devlog_key) for month in MONTHLY_DIRS
    ]
    targets.append((RELEASES, release_key))

    problems: list[str] = []
    for directory, key_of in targets:
        index = directory / "README.md"
        rows, found = collect(directory, index, key_of)
        problems.extend(found)
        if found and not args.check:
            rewrite(index, rows, key_of)
    for item in problems:
        print(f"  {item}")
    print(f"索引问题 {len(problems)} 处" + ("（--check 未写入）" if args.check else ""))
    return 1 if problems and args.check else 0


if __name__ == "__main__":
    raise SystemExit(main())
