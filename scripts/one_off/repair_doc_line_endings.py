"""一次性修复：把「混合换行」文档重建为逐行保留原换行的版本。

背景：`strip_doc_timestamps.py` / `fix_heading_spacing.py` 第一次运行时按平台默认换行
整份写回，把 HEAD 里本来的 `\\n` 行一起改成了 `\\r\\n`，于是「改两行」变成「整段重排」。
本脚本不改语义：以 HEAD 字节为基准重新施加同一组改动（删时效锚、去状态行日期基线、
标题后补空行），每一行沿用自己在 HEAD 里的换行，只对真正要改的行动手。

用法（仓库根、仓库 .venv）：

    python scripts/one_off/repair_doc_line_endings.py --check
    python scripts/one_off/repair_doc_line_endings.py
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

STAMP = re.compile(
    r"^>[ \t]*最后更新[：:][ \t]*\d{4}-\d{2}-\d{2}[ \t]*(?:（[^）]*）)?[ \t]*。?[ \t]*")
MID_STAMP = re.compile(
    r"[ \t]*最后更新[：:][ \t]*\d{4}-\d{2}-\d{2}[ \t]*(?:（[^）]*）)?[ \t]*。?")
STATUS_STAMP = re.compile(
    r"(?m)^(>[ \t]*状态[：:][ \t]*(?:已实现|部分实现|规划))[ \t]*"
    r"（\d{4}-\d{2}-\d{2}[，,][ \t]*基线[ \t]*v?\d+(?:\.\d+){1,2}）")
CODE_CAPTION = re.compile(r"^[^\s]*[./\\][^\s]*$")


def ending_of(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    return "\n" if line.endswith("\n") else ""


def transform(text: str) -> str:
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        if "最后更新" in line:
            stripped = STAMP.sub("", line)
            if stripped != line:
                rest = stripped.strip()
                if rest in ("", ">"):
                    continue
                fixed = rest if rest.startswith(">") else "> " + rest
                out.append(fixed + ending_of(line))
                continue
            bare = MID_STAMP.sub("", line).rstrip("\r\n")
            out.append(bare + ending_of(line))
            continue
        out.append(STATUS_STAMP.sub(r"\1", line))

    collapsed: list[str] = []
    for line in out:
        if not line.strip() and collapsed and not collapsed[-1].strip():
            continue
        collapsed.append(line)

    for index, line in enumerate(collapsed):
        if not line.startswith("# ") or CODE_CAPTION.match(line[2:].strip()):
            continue
        if index + 1 < len(collapsed) and collapsed[index + 1].strip():
            collapsed.insert(index + 1, ending_of(line) or "\n")
        break
    return "".join(collapsed)


def head_bytes(rel: str) -> bytes | None:
    result = subprocess.run(["git", "-C", str(REPO), "cat-file", "blob", f"HEAD:{rel}"],
                            capture_output=True)
    return result.stdout if result.returncode == 0 else None


def main() -> int:
    parser = argparse.ArgumentParser(description="按 HEAD 换行重建被改写的文档")
    parser.add_argument("--check", action="store_true", help="只报告不写入")
    args = parser.parse_args()

    changed = subprocess.run(["git", "-C", str(REPO), "diff", "--cached", "--name-only",
                              "--", "docs"],
                             capture_output=True, text=True, encoding="utf-8").stdout.split()
    repaired = 0
    for rel in changed:
        path = REPO / rel
        if not path.is_file() or path.suffix != ".md":
            continue
        head = head_bytes(rel)
        if head is None:
            continue
        text = head.decode("utf-8")
        if "\r\n" not in text:
            continue          # 整份 LF：原本就没被换行污染
        if text.replace("\r\n", "").count("\n") == 0:
            continue          # 整份 CRLF：与文件自身约定一致，不动
        rebuilt = transform(text)
        current = path.read_text(encoding="utf-8")
        if rebuilt.splitlines() == current.splitlines():
            print(f"语义一致，仅对齐换行：{rel}")
        else:
            print(f"语义有差异（需人工确认）：{rel}")
        repaired += 1
        if not args.check:
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(rebuilt)
    print(f"处理 {repaired} 份；{'仅报告' if args.check else '已重建'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
