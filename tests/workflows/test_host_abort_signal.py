"""宿主中止信号在引擎边界的记账方式。

无人值守批量撞上 pause 时，宿主当场抛 WorkflowAbort 改道。这是无人值守下的
**预期分支**，不是脚本或引擎出错，所以引擎只负责让它穿透：既不能在每层嵌套
各打一遍 ERROR + traceback（一次正常改道会被渲染成连环崩溃，掩盖真正的失败），
也不能被业务脚本的 try/catch 或 Python 工作流边界吃掉。
"""

import pytest

from lvjiang.workflows.errors import WorkflowAbort, WorkflowExecutionError
from lvjiang.workflows.grammar import parse_text
from tests.workflows.conftest import make_engine


def _engine_aborting_on_pause(monkeypatch):
    """按真实路径构造：pause 经 _ui_callback 走到宿主，宿主抛中止。"""
    errors: list = []
    monkeypatch.setattr(
        "lvjiang.workflows.engine.core.logger.error",
        lambda *args, **kwargs: errors.append(args[0] if args else ""))
    engine = make_engine()

    def _abort(kind, **kwargs):
        raise WorkflowAbort(str(kwargs.get("message") or kind))

    engine._ui_callback = _abort
    return engine, errors


def test_abort_propagates_without_being_logged_as_an_error(monkeypatch):
    """嵌套多层也只是穿透，一行 ERROR 都不该出现。"""
    engine, errors = _engine_aborting_on_pause(monkeypatch)
    program = parse_text(
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
    engine._procs = dict(program.procs)

    with pytest.raises(WorkflowAbort, match="未进入菜单页"):
        engine._exec_body(program.body)
    assert errors == []


def test_abort_is_not_swallowed_by_a_script_try_catch(monkeypatch):
    """脚本的兜底 catch 吞掉它，无人值守就会被消化成「成功」。"""
    engine, _errors = _engine_aborting_on_pause(monkeypatch)
    program = parse_text(
        'eval $caught = 0\n'
        'try\n'
        '    eval $x = pause("未进入菜单页")\n'
        'catch $err\n'
        '    eval $caught = 1\n'
        'end\n'
    )
    engine._procs = dict(program.procs)

    with pytest.raises(WorkflowAbort):
        engine._exec_body(program.body)
    assert engine.variables["caught"] == 0


class _AbortingWorkflow:
    def __init__(self):
        self.variables: dict = {}
        self.output: dict = {}
        self._reference_recognizer = None
        self._engine = None

    def reset_state(self):
        self.variables = {}
        self.output = {"processed": 2}

    def run(self):
        raise WorkflowAbort("未进入菜单页")


def test_python_workflow_boundary_passes_the_abort_through(monkeypatch):
    """包成 WorkflowExecutionError 会让宿主拿不到原始信号，按普通失败处理。"""
    errors: list = []
    monkeypatch.setattr(
        "lvjiang.workflows.engine.core.logger.error", errors.append)
    engine = make_engine()

    with pytest.raises(WorkflowAbort):
        engine._execute_python_workflow(_AbortingWorkflow())
    assert errors == []
    assert engine._workflow is None


def test_python_workflow_failure_still_wraps_as_execution_error():
    """普通失败的语义不变——上面那条分流不能顺手放过真异常。"""
    class _Failing(_AbortingWorkflow):
        def run(self):
            raise ValueError("boom")

    with pytest.raises(WorkflowExecutionError):
        make_engine()._execute_python_workflow(_Failing())
