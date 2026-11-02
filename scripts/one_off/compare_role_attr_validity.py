"""一次性验证：role_attrs.wf 右侧有效性判定重构前后行为等价。

背景：`capture_role_base_attrs` 里原来用两条 400+ 字符的 `and/or` 表达式判断
"外功区间完整"与"至少一门流派的属性攻击区间完整"，重构后改成
`attr_range_complete` / `attr_attack_has_school` 两个过程。语句数变了，
AST 逐棵比对不再适用，只能比对**行为**：

    对每个 key 存在组合，把旧版判定语句与新版判定语句各跑一遍，
    比较 `_right_outer_valid` / `_right_attr_attack_valid` 的取值与类型。

组合数是穷举的：4 个门派 × {缺 min / 缺 max / 都在 / 都缺} = 4^4 = 256，
外功另有 {都在 / 只 min / 只 max / 都缺} 4 种，两两组合共 1024 次执行。

`--mutate-max-key` 把新版里的 `max_` 改成 `maxx_` 再跑一遍：脚本必须报差异，
用来证明这套比对有分辨率（不是恒真）。等价性证明属于一次性验证，不进入
`tests/`。

基线固定为重构前的提交 `71a702ae`（最后一次仍带旧 `and/or` 表达式的版本）。
不能取 `git HEAD`：重构提交之后 HEAD 就是新版，再跑等于"新版跟自己比"，恒真。

用法：
    python scripts/one_off/compare_role_attr_validity.py
    python scripts/one_off/compare_role_attr_validity.py --base 71a702ae
    python scripts/one_off/compare_role_attr_validity.py --mutate-max-key
"""

from __future__ import annotations

import argparse
import itertools
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

_ROOT = Path(__file__).resolve().parents[2]
_WF = "config/system/workflows/subcall/loadout/role_attrs.wf"
_SCHOOLS = ("mingjin", "lieshi", "qiansi", "pozhu")
_FIELDS = ("_right_outer_valid", "_right_attr_attack_valid")
_CALL_VARS = {"outer_valid", "attr_attack_valid"}
# 重构前基线：该提交里 role_attrs.wf 仍是两条长 and/or 表达式。
_BASELINE_REV = "71a702ae"


@dataclass
class Version:
    """一个版本的判定语句：过程表 + 需要执行的语句切片。"""

    name: str
    procs: dict
    statements: list


def _parse(text: str):
    from lvjiang.workflows.grammar import parse_text

    return parse_text(text)


def _engine(procs: dict):
    """最小化引擎：判定语句只用到 has_key/字符串拼接，不触达截图与输入。"""
    from lvjiang.workflows.engine import WorkflowEngine

    capture = MagicMock()
    capture.get_capture_size.return_value = (1920, 1080)
    capture.min_capture_interval = 0.0
    layout = MagicMock()
    layout.get_canvas.return_value = MagicMock(
        x_ratio=0, y_ratio=0, w_ratio=1, h_ratio=1
    )
    engine = WorkflowEngine(
        capture=capture,
        ocr=MagicMock(),
        input_ctrl=MagicMock(),
        layout=layout,
        input_sim=MagicMock(),
        delay_params={},
    )
    engine._procs = dict(procs)
    return engine


def _judgement_slice(text: str, label: str) -> tuple[Version, list[str]]:
    """截出"计算两个 _right_*_valid 字段"的语句切片。

    旧版是两条相邻 Eval；新版是 call+eval 成对出现。按"涉及的语句"取首尾，
    不假设具体行号或行数。
    """
    from lvjiang.workflows.grammar.ast_nodes import (
        CallProc,
        EvalFieldChainAssign,
    )

    program = _parse(text)
    body = program.procs["capture_role_base_attrs"].body

    touched = []
    for index, node in enumerate(body):
        if (
            isinstance(node, EvalFieldChainAssign)
            and getattr(node.target, "field_name", None) in _FIELDS
        ):
            touched.append(index)
        elif isinstance(node, CallProc) and node.result_var in _CALL_VARS:
            touched.append(index)
    if not touched:
        raise SystemExit(f"{label}: 未找到判定语句")

    statements = body[min(touched): max(touched) + 1]
    kinds = [type(node).__name__ for node in statements]
    return Version(name=label, procs=program.procs, statements=list(statements)), kinds


def _baseline_text(rev: str) -> str:
    return subprocess.run(
        ["git", "show", f"{rev}:{_WF}"], cwd=_ROOT, capture_output=True, check=True
    ).stdout.decode("utf-8")


def _worktree_text(mutate: bool) -> str:
    text = (_ROOT / _WF).read_text(encoding="utf-8")
    if mutate:
        if '"max_"' not in text:
            raise SystemExit("--mutate-max-key: 新版里找不到 \"max_\" 前缀")
        text = text.replace('"max_"', '"maxx_"', 1)
    return text


def _detail(prefix: str, state: str) -> dict:
    """按状态造一份解析结果：缺 min / 缺 max / 都在 / 都缺。"""
    if state == "both":
        return {f"min_{prefix}": 1, f"max_{prefix}": 9}
    if state == "min":
        return {f"min_{prefix}": 1}
    if state == "max":
        return {f"max_{prefix}": 9}
    return {}


def _run(version: Version, outer_state: str, schools) -> dict:
    engine = _engine(version.procs)
    engine.variables = {
        "parsed": {},
        "outer_detail": _detail("outer", outer_state),
        "attr_attack_detail": {
            key: value
            for school, state in schools
            for key, value in _detail(school, state).items()
        },
    }
    engine._exec_body(version.statements)
    parsed = engine.variables["parsed"]
    return {field: (parsed[field], type(parsed[field]).__name__) for field in _FIELDS}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base", default=_BASELINE_REV,
        help=f"基线提交（默认 {_BASELINE_REV}，即重构前最后一次旧表达式版本）",
    )
    parser.add_argument(
        "--mutate-max-key", action="store_true",
        help="把新版的 max_ 前缀改坏，验证比对能发现差异",
    )
    args = parser.parse_args()

    old, old_kinds = _judgement_slice(
        _baseline_text(args.base), f"baseline({args.base})")
    new, new_kinds = _judgement_slice(_worktree_text(args.mutate_max_key), "worktree")
    print(f"基线语句: {old_kinds}")
    print(f"新版语句: {new_kinds}")

    states = ("none", "min", "max", "both")
    compared = 0
    mismatches = []
    for schools in itertools.product(states, repeat=len(_SCHOOLS)):
        pairs = list(zip(_SCHOOLS, schools, strict=True))
        for outer_state in states:
            compared += 1
            before = _run(old, outer_state, pairs)
            after = _run(new, outer_state, pairs)
            if before != after:
                mismatches.append((outer_state, schools, before, after))

    print(f"比较组合数: {compared}")
    if mismatches:
        print(f"发现差异 {len(mismatches)} 处，前 5 条：")
        for outer_state, schools, before, after in mismatches[:5]:
            print(f"  outer={outer_state} schools={schools}")
            print(f"    基线: {before}")
            print(f"    新版: {after}")
        return 1
    print("结果：两版在全部组合下取值与类型完全一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
