"""一次性迁移：剥离活跃文档里的「> 最后更新：…」与状态行的时间/基线括号。

文档生命周期改由目录表达（20-plans → 30-design → 40-reference），修改时间由 Git
表达，因此活跃层不再写日期与基线。50-releases/、80-history/ 属冻结存档，保持原样。

用法（在仓库根、用仓库 .venv）：

    python scripts/one_off/strip_doc_timestamps.py --check   # 只报告
    python scripts/one_off/strip_doc_timestamps.py           # 就地改写
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"

#: 参与清理的活跃层；50-releases/、80-history/ 是冻结存档，显式排除。
ACTIVE_DIRS = ("00-meta", "10-domain", "20-plans", "30-design", "40-reference",
               "60-guide", "70-agent")

#: `> 最后更新：2026-11-23（基线 v0.15.19）。` 与各种同义变体
STAMP = re.compile(
    r"^>[ \t]*最后更新[：:][ \t]*\d{4}-\d{2}-\d{2}[ \t]*(?:（[^）]*）)?[ \t]*。?[ \t]*")
#: 句中出现的时间戳，例如「…执行。最后更新：2026-11-14（仅改版本名）。」
MID_STAMP = re.compile(
    r"[ \t]*最后更新[：:][ \t]*\d{4}-\d{2}-\d{2}[ \t]*(?:（[^）]*）)?[ \t]*。?")
#: 状态行里的 `（2026-10-08，基线 v0.13.14）` 括号
STATUS_STAMP = re.compile(
    r"(?m)^(>[ \t]*状态[：:][ \t]*(?:已实现|部分实现|规划))[ \t]*"
    r"（\d{4}-\d{2}-\d{2}[，,][ \t]*基线[ \t]*v?\d+(?:\.\d+){1,2}）")


def targets() -> list[Path]:
    files = [DOCS / "README.md"]
    for name in ACTIVE_DIRS:
        files.extend(sorted((DOCS / name).rglob("*.md")))
    return [path for path in files if path.is_file()]


def rewrite(text: str) -> tuple[str, list[str]]:
    """返回改写后的正文与被改动的行（用于报告）。"""
    out: list[str] = []
    changed: list[str] = []
    for line in text.splitlines():
        if "最后更新" in line:
            stripped = STAMP.sub("", line)
            if stripped != line:
                rest = stripped.strip()
                if rest in ("", ">"):
                    changed.append(f"删除  {line.strip()}")
                    continue
                fixed = rest if rest.startswith(">") else "> " + rest
                changed.append(f"缩减  {line.strip()}  ->  {fixed}")
                out.append(fixed)
                continue
            stripped = MID_STAMP.sub("", line).rstrip()
            if stripped != line:
                changed.append(f"缩减  {line.strip()}  ->  {stripped}")
            out.append(stripped)
            continue
        fixed = STATUS_STAMP.sub(r"\1", line)
        if fixed != line:
            changed.append(f"缩减  {line.strip()}  ->  {fixed.strip()}")
        out.append(fixed)
    # 删行会留下空档：连续空行收成一个，标题下不留双空行
    collapsed: list[str] = []
    for line in out:
        if not line.strip() and collapsed and not collapsed[-1].strip():
            continue
        collapsed.append(line)
    body = "\n".join(collapsed)
    return body + ("\n" if text.endswith("\n") else ""), changed


def main() -> int:
    parser = argparse.ArgumentParser(description="剥离活跃文档的日期与基线")
    parser.add_argument("--check", action="store_true", help="只报告不写入")
    args = parser.parse_args()

    total = 0
    for path in targets():
        text = path.read_text(encoding="utf-8")
        new, changed = rewrite(text)
        if not changed:
            continue
        total += len(changed)
        rel = path.relative_to(REPO).as_posix()
        print(f"== {rel} ==")
        for item in changed:
            print(f"  {item}")
        if not args.check and new != text:
            # 保留原换行：Windows 上 write_text 会把 LF 翻成 CRLF，制造整文件假 diff。
            path.write_text(new, encoding="utf-8", newline="")
    print(f"共 {total} 处；{'仅报告' if args.check else '已写入'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
