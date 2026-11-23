"""一次性格式修正：一级标题下一行必须是空行。

标题紧贴正文（常见于 `# 标题` 紧接 `> 状态：…`）会让渲染与纯文本阅读都挤在一起。
本脚本对 `docs/` 下全部 `.md` 找到第一处 `# ` 标题，若下一行非空就补一个空行；
换行风格（LF/CRLF）沿用该标题行本身，不重排其他内容。

用法（仓库根、仓库 .venv）：

    python scripts/one_off/fix_heading_spacing.py --check
    python scripts/one_off/fix_heading_spacing.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"


def fix(path: Path) -> tuple[str, str] | None:
    """返回 (原文, 修正后)；无需修改时返回 None。"""
    with open(path, encoding="utf-8", newline="") as handle:
        text = handle.read()
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not line.startswith("# "):
            continue
        if index + 1 >= len(lines):
            return None
        following = lines[index + 1]
        if not following.strip():
            return None
        newline = "\r\n" if line.endswith("\r\n") else "\n"
        fixed = "".join(lines[:index + 1]) + newline + "".join(lines[index + 1:])
        return text, fixed
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="标题后补空行")
    parser.add_argument("--check", action="store_true", help="只报告不写入")
    args = parser.parse_args()

    total = 0
    for path in sorted(DOCS.rglob("*.md")):
        result = fix(path)
        if result is None:
            continue
        total += 1
        print(path.relative_to(REPO).as_posix())
        if not args.check:
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(result[1])
    print(f"共 {total} 份；{'仅报告' if args.check else '已补空行'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
