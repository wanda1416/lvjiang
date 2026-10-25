"""DSL 语句失败的记账类别。

预期结果（`profile_reject` 的业务拒绝、脚本自身的可预期错误）消息里已经写清
原因和行号，日志只记一行；只有意外异常才附堆栈。两类都不许按嵌套层数重复
记账——一次失败被打印成连环崩溃会掩盖真正的失败。
"""

import pytest
from loguru import logger as loguru_logger

from lvjiang.core.profile.transactions import ProfileWriteRejected
from lvjiang.workflows.errors import WorkflowUserError
from lvjiang.workflows.grammar import parse_text
from tests.workflows.conftest import make_engine

# 三层嵌套（子过程 → if 体 → 顶层），逐层记账会把一次失败打印三遍。
NESTED_SCRIPT = (
    'def inner()\n'
    '    eval $x = pause("未进入菜单页")\n'
    'end\n'
    'def outer()\n'
    '    if 1\n'
    '        call inner()\n'
    '    end\n'
    'end\n'
    'call outer()\n'
)


@pytest.fixture
def records():
    """收集本次执行写出的日志行，格式为 ``级别 | 文本``。"""
    lines: list[str] = []
    sink_id = loguru_logger.add(
        lambda message: lines.append(str(message).strip()),
        level="DEBUG", format="{level} | {message}",
    )
    try:
        yield lines
    finally:
        loguru_logger.remove(sink_id)


def _run_nested_raising(error: BaseException) -> None:
    """在嵌套脚本最内层抛出指定异常，断言它原样穿透到调用方。"""
    engine = make_engine()

    def _raise(kind, **_kwargs):
        raise error

    engine._ui_callback = _raise
    program = parse_text(NESTED_SCRIPT)
    engine._procs = dict(program.procs)
    with pytest.raises(type(error)):
        engine._exec_body(program.body)


def _dsl_lines(records: list[str]) -> list[str]:
    return [line for line in records if "DSL" in line]


def test_rejected_write_logs_one_warning_line_without_traceback(records):
    """业务拒绝是预期结果：一行 WARNING，没有堆栈。"""
    _run_nested_raising(ProfileWriteRejected("承音石获取超过来源上限：武墓证道"))

    logged = _dsl_lines(records)
    assert len(logged) == 1
    assert logged[0].startswith("WARNING | ")
    assert "承音石获取超过来源上限：武墓证道" in logged[0]
    assert not any("Traceback" in line for line in records)


def test_script_error_logs_one_error_line_without_traceback(records):
    """脚本可预期错误要用户改脚本，级别仍是 ERROR，但堆栈只有引擎内部帧。"""
    _run_nested_raising(WorkflowUserError("变量 $missing 未定义"))

    logged = _dsl_lines(records)
    assert len(logged) == 1
    assert logged[0].startswith("ERROR | ")
    assert "变量 $missing 未定义" in logged[0]
    assert not any("Traceback" in line for line in records)


def test_unexpected_error_keeps_exactly_one_traceback(records):
    """意外异常必须附堆栈，且逐层穿透不能重复记账。"""
    _run_nested_raising(ValueError("boom"))

    errors = [line for line in records if line.startswith("ERROR | ")]
    assert len(errors) == 2  # 一行摘要 + 异常详情
    assert "DSL 执行异常" in errors[0] and "boom" in errors[0]
    assert sum("Traceback" in line for line in records) == 1
