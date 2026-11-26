"""把 i18n 表里的多行显式键改写成「双引号 + \\n 转义」的单行条目。

   ? '已导出到            →    "已导出到\\n{path}": "已导出到\\n{path}"
   (空行)
       {path}'
   : '已导出到
   (空行)
       {path}'

YAML 双引号标量会把 `\\n` 解释成换行，因此 key 解析结果与源码里含换行的字面量
仍然相等；只是不再需要把一句话摊成多行。改写前后用 safe_load 对比，确保映射完全
一致。
"""
from __future__ import annotations

import argparse
import json
import pathlib

import yaml

ROOT = pathlib.Path("config/i18n/apps/yysls")


def _continuation(line: str) -> bool:
    return line.strip() == "" or line.startswith("    ")


def _blocks(lines: list[str]) -> list[tuple[int, int]]:
    """返回 (start, end) 半开区间，覆盖 `? …` key 段 + `: …` value 段。"""
    spans: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        if not lines[i].startswith("  ? "):
            i += 1
            continue
        j = i + 1
        while j < len(lines) and _continuation(lines[j]):
            j += 1
        if j >= len(lines) or not lines[j].startswith("  : "):
            raise SystemExit(f"unexpected block at line {i + 1}: {lines[i]!r}")
        j += 1
        while j < len(lines) and _continuation(lines[j]):
            j += 1
        spans.append((i, j))
        i = j
    return spans


def _parse_block(block: list[str]) -> tuple[str, str]:
    indented = [("  " + line) if line.strip() else "" for line in block]
    doc = yaml.safe_load("x:\n" + "\n".join(indented))
    key, value = next(iter(doc["x"].items()))
    if not isinstance(key, str) or not isinstance(value, str):
        raise SystemExit(f"non-string entry: {key!r} -> {value!r}")
    return key, value


def _dq(text: str) -> str:
    """YAML 双引号标量：转义反斜杠、双引号与控制字符。"""
    escaped = json.dumps(text, ensure_ascii=False)[1:-1]
    return f'"{escaped}"'


def main() -> None:
    parser = argparse.ArgumentParser(description="把 i18n 多行条目改写为 \\n 转义单行")
    parser.add_argument("--apply", action="store_true", help="真正写回；默认只报告")
    args = parser.parse_args()

    report = []
    for path in sorted(ROOT.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        spans = _blocks(lines)
        if not spans:
            report.append(f"{path.name}: blocks=0 (already normalized)")
            continue
        out = list(lines)
        for start, end in reversed(spans):
            key, value = _parse_block(lines[start:end])
            out[start:end] = [f"  {_dq(key)}: {_dq(value)}"]
        new_text = "\n".join(out) + "\n"
        if yaml.safe_load(text) != yaml.safe_load(new_text):
            raise SystemExit(f"{path.name}: mapping would change, skipped")
        if args.apply:
            path.write_text(new_text, encoding="utf-8", newline="\n")
            report.append(f"{path.name}: blocks={len(spans)} rewritten, mapping_ok")
        else:
            report.append(f"{path.name}: blocks={len(spans)} (dry-run, use --apply)")
    summary = "\n".join(report)
    pathlib.Path(".tmp").mkdir(exist_ok=True)
    pathlib.Path(".tmp/normalize_i18n_report.txt").write_text(summary + "\n", encoding="utf-8")
    print(summary)


if __name__ == "__main__":
    main()
