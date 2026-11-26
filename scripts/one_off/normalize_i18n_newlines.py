"""把翻译表里的跨行条目改写成「双引号 + 转义」的单行条目。

抽词表的生成器遇到长文案或含换行的字面量时，会写出三种跨行条目：

1. 显式键 + 换行（最丑的三段式）::

       ? '第一行
       (空行)
           第二行'
       : '第一行
       (空行)
           第二行'

2. 显式键但每段只有一行（key 与 value 各占一行）::

       ? "长 key"
       : "长 value"

3. 普通标量被折行（YAML 把折行变成空格）::

         确定删除「{name}」吗？…: Supprimer « {name} »
           Les {count} références … seront détachées

三种都改写成一行 ``"key": "value"``，换行按 YAML 双引号语义写成 ``\\n``。改写前后
用 ``yaml.safe_load`` 对比整份文档，映射有任何差别就保留原文件并报错退出。

用法::

    python scripts/one_off/normalize_i18n_newlines.py            # 干跑，只报数量
    python scripts/one_off/normalize_i18n_newlines.py --apply    # 写回
    python scripts/one_off/normalize_i18n_newlines.py --root config/i18n/apps
"""
from __future__ import annotations

import argparse
import json
import pathlib

import yaml

DEFAULT_ROOT = pathlib.Path("config/i18n")
#: 以这些字符开头的标量是容器或块标量，不参与「标量折行」判定
CONTAINER_STARTS = ("[", "{", "|", ">", "&", "*", "!")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_blank(line: str) -> bool:
    return line.strip() == ""


def _indented_block(block: list[str]) -> str:
    """把块整体右移两格，套进 ``x:`` 下做成合法的单条映射。"""
    return "\n".join(("  " + line) if line.strip() else "" for line in block)


def _parse_block(block: list[str]) -> tuple[str, str]:
    doc = yaml.safe_load("x:\n" + _indented_block(block))
    key, value = next(iter(doc["x"].items()))
    if not isinstance(key, str) or not isinstance(value, str):
        raise ValueError(f"词条不是字符串: {key!r} -> {value!r}")
    return key, value


def _dq(text: str) -> str:
    """YAML 双引号标量：转义反斜杠、双引号与控制字符。"""
    return '"' + json.dumps(text, ensure_ascii=False)[1:-1] + '"'


def _scalar_may_continue(rest: str) -> bool:
    """``key: value`` 的 value 已开始，且不是容器或块标量。"""
    _, sep, value = rest.partition(":")
    if not sep:
        return False
    value = value.strip()
    return bool(value) and not value.startswith(CONTAINER_STARTS)


def _block_span(lines: list[str], start: int) -> int:
    """从 ``start`` 起的跨行条目结束位置（不含）；只看缩进结构，不解析内容。"""
    indent = _indent(lines[start])
    rest = lines[start][indent:]
    cursor = start + 1

    def more() -> bool:
        return cursor < len(lines) and (
            _is_blank(lines[cursor]) or _indent(lines[cursor]) > indent
        )

    if rest.startswith("? "):
        while more():
            cursor += 1
        if cursor < len(lines) and lines[cursor][indent:].startswith(": "):
            cursor += 1
            while more():
                cursor += 1
        return cursor
    if _scalar_may_continue(rest):
        while more():
            cursor += 1
    return cursor


def rewrite(lines: list[str]) -> tuple[list[str], int]:
    """把跨行条目压成单行；返回新行与改写数量。"""
    out: list[str] = []
    count = 0
    index = 0
    while index < len(lines):
        line = lines[index]
        if _is_blank(line) or line.lstrip().startswith("#"):
            out.append(line)
            index += 1
            continue
        end = _block_span(lines, index)
        if end <= index + 1:
            out.append(line)
            index += 1
            continue
        key, value = _parse_block(lines[index:end])
        out.append(f"{' ' * _indent(line)}{_dq(key)}: {_dq(value)}")
        count += 1
        index = end
    return out, count


def main() -> None:
    parser = argparse.ArgumentParser(description="把翻译表跨行条目改写为单行转义条目")
    parser.add_argument("--apply", action="store_true", help="真正写回；默认只报告")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="词表根目录")
    args = parser.parse_args()

    root = pathlib.Path(args.root)
    report: list[str] = []
    total_files = total_entries = 0
    failures: list[str] = []
    for path in sorted(root.rglob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        new_lines, count = rewrite(text.splitlines())
        if not count:
            continue
        total_files += 1
        total_entries += count
        new_text = "\n".join(new_lines) + "\n"
        if yaml.safe_load(text) != yaml.safe_load(new_text):
            failures.append(f"{path}: mapping would change, left untouched")
            continue
        if args.apply:
            path.write_text(new_text, encoding="utf-8", newline="\n")
        report.append(f"{path.relative_to(root)}: {count} entries")

    summary = [
        *report,
        *failures,
        f"{'rewritten' if args.apply else 'dry-run'}: files={total_files} entries={total_entries}",
    ]
    text_out = "\n".join(summary)
    pathlib.Path(".tmp").mkdir(exist_ok=True)
    pathlib.Path(".tmp/normalize_i18n_report.txt").write_text(text_out + "\n", encoding="utf-8")
    print(summary[-1])
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
