"""paste DSL 解析与执行测试。"""

import pytest

from lvjiang.workflows.engine.signals import WorkflowUserError
from lvjiang.workflows.grammar import Paste, Wait, parse_text
from tests.workflows.conftest import make_engine


def _execute(code: str, variables: dict | None = None):
    engine = make_engine()
    engine.variables = dict(variables or {})
    program = parse_text(code)
    engine._exec_body(program.body)
    return engine, program


def test_parse_literal_and_wait_clause():
    program = parse_text('paste "ABC123" after wait 0.5\n')

    assert isinstance(program.body[0], Paste)
    assert program.body[0].value.value == "ABC123"
    assert isinstance(program.body[1], Wait)


def test_paste_variable_calls_input_backend():
    engine, _ = _execute('paste $redeem_code\n', {"redeem_code": "礼-ABC-123"})

    engine._input.paste_text.assert_called_once_with("礼-ABC-123")


def test_paste_supports_string_expression():
    engine, _ = _execute('paste "ABC" + $suffix\n', {"suffix": 123})

    engine._input.paste_text.assert_called_once_with("ABC123")


def test_paste_null_is_rejected():
    with pytest.raises(WorkflowUserError, match="未定义或值为 null"):
        _execute('paste $missing\n')


def test_paste_unsupported_backend_is_user_error():
    engine = make_engine()
    engine._input.paste_text.side_effect = NotImplementedError("ADB 不支持粘贴文本")
    program = parse_text('paste "ABC123"\n')

    with pytest.raises(WorkflowUserError, match="ADB 不支持"):
        engine._exec_body(program.body)


def test_env_guard_accepts_paste():
    program = parse_text('env:"desktop" -> paste $code\n')

    assert isinstance(program.body[0].then_body[0], Paste)
