"""过程作用域与参数默认值静态检查的回归。

两类问题都不会让脚本报错，只会让运行时读到 null / 走另一套默认值，
所以在加载期拦下来。
"""

import pytest

from lvjiang.workflows.engine.signals import WorkflowUserError
from lvjiang.workflows.grammar import parse_text
from lvjiang.workflows.metadata import WorkflowMetadataError, parse_metadata
from lvjiang.workflows.program_check import (
    check_metadata_defaults,
    check_proc_scope_reads,
    collect_global_names,
)
from tests.workflows.conftest import make_engine


def _scope_problems(code: str, *, reachable_only: bool = False):
    program = parse_text(code)
    names = collect_global_names(
        program.body, *(p.body for p in program.procs.values()))
    return check_proc_scope_reads(
        program, program.procs, global_names=names,
        reachable_only=reachable_only)


# ─── 过程作用域 ────────────────────────────────────────────


def test_scope_accepts_params_assigned_and_global():
    """形参、本过程赋值目标、global 声明都是合法来源。"""
    code = (
        "global $shared\n"
        "def f($p)\n"
        "    eval $local = $p + 1\n"
        "    for item in [$local]\n"
        "        scan [s].[r] as $scanned\n"
        "        eval $local = $scanned.r\n"
        "    end\n"
        "    call g($shared)\n"
        "    return $local\n"
        "end\n"
    )
    assert _scope_problems(code) == []


def test_scope_reports_missing_global():
    """漏写 global 是静默失效的典型来源。"""
    code = (
        "eval $threshold = 0.65\n"
        "def f()\n"
        "    return $threshold\n"
        "end\n"
    )
    problems = _scope_problems(code)
    assert [p.variable for p in problems] == ["threshold"]
    assert problems[0].proc == "f"


def test_scope_reports_typo():
    code = (
        "def f($items)\n"
        "    eval $total = 0\n"
        "    for item in $items\n"
        "        eval $total = $totl + 1\n"
        "    end\n"
        "    return $total\n"
        "end\n"
    )
    assert [p.variable for p in _scope_problems(code)] == ["totl"]


def test_field_assign_does_not_define_the_root():
    """字段写入不创建根变量。先读后写字段、又没有 global 时必须报出来。"""
    code = (
        "def f()\n"
        "    if $record_result\n"
        "        eval $record_result.status = 1\n"
        "    end\n"
        "end\n"
    )
    problems = _scope_problems(code)
    assert [p.variable for p in problems] == ["record_result"]
    assert problems[0].proc == "f"


def test_assign_then_field_write_is_in_scope():
    """本过程里先赋过值的字典，随后写字段是合法的。"""
    code = (
        "def f()\n"
        "    eval $record_result = {}\n"
        "    eval $record_result.status = 1\n"
        "end\n"
    )
    assert _scope_problems(code) == []


def test_scope_reachable_only_skips_unused_proc():
    """只查可达过程时，没人调用的过程不参与判定。"""
    code = (
        "def unused()\n"
        "    return $missing\n"
        "end\n"
        "def used()\n"
        "    return 0\n"
        "end\n"
        "call used()\n"
    )
    assert _scope_problems(code, reachable_only=True) == []
    assert len(_scope_problems(code)) == 1


def test_validate_only_ignores_unreached_library_scope(wf_root):
    """预检不把没调用到的库过程算进作用域。

    result_records 一类库允许调用方不声明 global，辅助过程直接空操作。
    只 import 并调用无关过程时，不应要求写上用不到的 global。
    真正 call 到那个过程时，漏掉的 global 仍然要报。
    """
    library = wf_root / "lib.wf"
    library.write_text(
        "def unused()\n"
        "    if $record_result\n"
        "        eval $record_result.status = 1\n"
        "    end\n"
        "end\n"
        "def used()\n"
        "    return 0\n"
        "end\n",
        encoding="utf-8",
    )
    caller = wf_root / "caller.wf"
    caller.write_text('import "lib.wf"\ncall used()\n', encoding="utf-8")
    make_engine().validate_only(caller)

    caller.write_text('import "lib.wf"\ncall unused()\n', encoding="utf-8")
    with pytest.raises(WorkflowUserError, match="record_result"):
        make_engine().validate_only(caller)


# ─── metadata 默认值 ───────────────────────────────────────


def _default_problems(meta_text: str, code: str):
    return check_metadata_defaults(parse_metadata(meta_text), parse_text(code))


def test_default_mismatch_is_reported():
    problems = _default_problems(
        "#% parameters:\n"
        "#%   - name: max_scroll\n"
        "#%     label: 次数\n"
        "#%     type: number\n"
        "#%     default: 12\n",
        "default $max_scroll = 2\n",
    )
    assert [p.parameter for p in problems] == ["max_scroll"]
    assert problems[0].metadata_value == 12
    assert problems[0].code_value == 2


def test_default_match_is_clean():
    assert _default_problems(
        "#% parameters:\n"
        "#%   - name: max_scroll\n"
        "#%     label: 次数\n"
        "#%     type: number\n"
        "#%     default: 12\n",
        "default $max_scroll = 12\n",
    ) == []


def test_default_string_ignores_quote_style():
    assert _default_problems(
        "#% parameters:\n"
        "#%   - name: mode\n"
        "#%     label: 模式\n"
        "#%     type: text\n"
        '#%     default: "fast"\n',
        'default $mode = "fast"\n',
    ) == []


def test_default_multiline_block_is_not_compared():
    """``|-`` 多行块交给人工核对，不做文本比对以免误报。"""
    assert _default_problems(
        "#% parameters:\n"
        "#%   - name: items\n"
        "#%     label: 列表\n"
        "#%     type: text\n"
        "#%     default: |-\n"
        "#%       第一行\n"
        "#%       第二行\n",
        'default $items = "第一行 第二行"\n',
    ) == []


def test_unknown_parameter_type_is_rejected():
    """过去未知 type 会把参数静默丢弃（UI 不显示、批量不注入）。"""
    with pytest.raises(WorkflowMetadataError):
        parse_metadata(
            "#% parameters:\n"
            "#%   - name: n\n"
            "#%     label: 次数\n"
            "#%     type: int\n"
            "#%     default: 1\n"
        )
