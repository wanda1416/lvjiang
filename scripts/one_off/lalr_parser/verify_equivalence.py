"""手动对照指定 Git 基线的 Earley 与当前解析器；不进入持续运行或 CI。"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from dataclasses import fields, is_dataclass
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))


def shape(value):
    """比较全部 dataclass 字段，包括 compare=False 的源位置，且区分类型。"""
    if is_dataclass(value):
        return type(value).__name__, tuple(
            (field.name, shape(getattr(value, field.name))) for field in fields(value)
        )
    if isinstance(value, dict):
        return type(value).__name__, {key: shape(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return type(value).__name__, tuple(shape(item) for item in value)
    return type(value).__name__, value


def main() -> int:
    from lark import Lark
    from lark.exceptions import UnexpectedInput

    from lvjiang.workflows.grammar.ast_nodes import Program
    from lvjiang.workflows.grammar.parser.api import (
        _get_parser,
        _preprocess_line_continuation,
        parse_text,
    )
    from lvjiang.workflows.grammar.parser.transformer import _DSLTransformer

    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--baseline-ref", required=True, help="改造前的 Git 提交")
    args = cli.parse_args()
    grammar = subprocess.run(
        ["git", "show", f"{args.baseline_ref}:src/lvjiang/workflows/grammar/grammar.lark"],
        cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout
    old_parser = Lark(grammar, parser="earley", propagate_positions=True)
    _get_parser()  # 建表成本单独于逐文件解析计时。

    def old_parse(original: str, source: str) -> Program:
        text, origin = _preprocess_line_continuation(original)
        if not text.endswith("\n"):
            text += "\n"
            origin.append(-1)
        transformer = _DSLTransformer()
        transformer.set_source_map(original, text, origin)
        program = transformer.transform(old_parser.parse(text))
        return Program(body=program.body, imports=program.imports,
                       procs=program.procs, source=source)

    cases: dict[tuple[str, str], str] = {}
    for directory in (ROOT / "config/system/workflows", ROOT / "config/local/workflows"):
        for path in sorted(directory.rglob("*.wf")):
            cases[(str(path.relative_to(ROOT)), "file")] = path.read_text(encoding="utf-8-sig")
    # 常量片段覆盖真实脚本尚未用到的语法及非法输入，不执行任何工作流。
    for path in sorted((ROOT / "tests/workflows").rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else None
            if name != "parse_text" or not isinstance(node.args[0], ast.Constant):
                continue
            text = node.args[0].value
            if isinstance(text, str):
                cases[(text, "snippet")] = text

    mismatches = []
    times = {"earley": 0.0, "current": 0.0}
    accepted = {"file": 0, "snippet": 0}
    rejected = 0
    for (label, kind), text in cases.items():
        outputs = []
        for name, parser in (("earley", old_parse), ("current", parse_text)):
            start = perf_counter()
            try:
                outputs.append(("ok", shape(parser(text, source="<equivalence>"))))
            except UnexpectedInput as error:
                outputs.append(("syntax_error", error.line, error.column))
            except Exception as error:
                outputs.append((type(error).__name__, str(error)))
            times[name] += perf_counter() - start
        if outputs[0] != outputs[1]:
            # 不输出 local 脚本内容，避免把私人数据写入报告。
            mismatches.append(label if kind == "file" else "测试常量片段")
        elif outputs[0][0] == "ok":
            accepted[kind] += 1
        else:
            rejected += 1
    print(f"一致的工作流 AST：{accepted['file']}；测试片段 AST：{accepted['snippet']}；拒绝输入：{rejected}")
    print(f"Earley：{times['earley']:.3f}s；当前：{times['current']:.3f}s")
    print(f"差异数量：{len(mismatches)}")
    for label in mismatches:
        print(f"需复核：{label}")
    return bool(mismatches)


if __name__ == "__main__":
    raise SystemExit(main())
