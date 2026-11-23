"""一次性修复：把文档迁移时被换成 CRLF 的正文改回 LF。

`strip_doc_timestamps.py` 首版用 `Path.write_text` 写入，Windows 上会把 LF 翻成
CRLF，产生「改一行、整份重排」的假 diff。本脚本只处理 HEAD 里完全没有 CRLF 的
文档，把工作区里的 CRLF 还原成 LF；HEAD 本身就是 CRLF 的历史文档保持原样。

用法（仓库根、仓库 .venv）：

    python scripts/one_off/normalize_doc_line_endings.py --check
    python scripts/one_off/normalize_doc_line_endings.py
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def git(*args: str) -> bytes:
    return subprocess.run(["git", "-C", str(REPO), *args],
                          capture_output=True, check=True).stdout


def head_bytes(rel: str, renames: dict[str, str]) -> bytes | None:
    """取该文件在 HEAD 的字节；改名的文件按旧路径取。"""
    for candidate in (rel, renames.get(rel, "")):
        if not candidate:
            continue
        result = subprocess.run(["git", "-C", str(REPO), "show", f"HEAD:{candidate}"],
                                capture_output=True)
        if result.returncode == 0:
            return result.stdout
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="还原文档换行")
    parser.add_argument("--check", action="store_true", help="只报告不写入")
    args = parser.parse_args()

    changed = git("diff", "--name-only", "--", "docs").decode("utf-8").split()
    renames: dict[str, str] = {}
    for line in git("diff", "--cached", "--name-status", "-M", "--", "docs").decode(
            "utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].startswith("R"):
            renames[parts[2]] = parts[1]
    fixed: list[str] = []
    for rel in changed:
        path = REPO / rel
        if not path.is_file() or path.suffix != ".md":
            continue
        head = head_bytes(rel, renames)
        if head is None or b"\r\n" in head:
            continue
        data = path.read_bytes()
        if b"\r\n" not in data:
            continue
        fixed.append(rel)
        if not args.check:
            path.write_bytes(data.replace(b"\r\n", b"\n"))
    for rel in fixed:
        print(rel)
    print(f"共 {len(fixed)} 份；{'仅报告' if args.check else '已还原为 LF'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
