"""Python 工作流停止信号的引擎边界测试。"""

import pytest

from lvjiang.workflows.engine import WorkflowEngine
from lvjiang.workflows.errors import WorkflowExecutionError
from lvjiang.workflows.grammar import parse_text
from tests.workflows.conftest import make_engine


class _StoppingWorkflow:
    def __init__(self):
        self.variables = {}
        self.output = {}
        self._reference_recognizer = None
        self._engine: WorkflowEngine | None = None

    def reset_state(self):
        self.variables = {}
        self.output = {"processed": 3}

    def run(self):
        assert self._engine is not None
        self._engine.call_subcall("stop_here")
        return self.output


class _FailingWorkflow(_StoppingWorkflow):
    def run(self):
        self.output["processed"] = 4
        raise ValueError("boom")


def test_python_workflow_treats_break_signal_as_normal_stop(monkeypatch):
    """DSL 子调用传播的停止信号不应被记录为 Python 异常。"""
    errors = []
    monkeypatch.setattr(
        "lvjiang.workflows.engine.core.logger.error", errors.append)
    stop_checks = iter((False, True))
    engine = make_engine(stop_check=lambda: next(stop_checks, True))
    engine._procs = parse_text(
        'def stop_here()\n    log "never reached"\nend\n').procs

    result = engine._execute_python_workflow(_StoppingWorkflow())

    assert result == {"processed": 3}
    assert errors == []
    assert engine._workflow is None


def test_python_workflow_propagates_failure_with_partial_output():
    """意外异常不能降级成正常结果，但应保留排障所需的部分输出。"""
    engine = make_engine()

    with pytest.raises(WorkflowExecutionError) as caught:
        engine._execute_python_workflow(_FailingWorkflow())

    assert caught.value.workflow_name == "_FailingWorkflow"
    assert caught.value.partial_output == {"processed": 4}
    assert isinstance(caught.value.__cause__, ValueError)
    assert engine._workflow is None


def test_partial_output_is_logged_before_failure_propagates(caplog):
    """失败前的输出必须留在日志里——异常路径不落盘 output/ 下的 JSON。"""

    from lvjiang.workflows.engine import core as core_mod

    engine = make_engine()
    records: list[str] = []
    handler = core_mod.logger.add(
        lambda m: records.append(m), level="ERROR", format="{message}")
    try:
        with pytest.raises(WorkflowExecutionError):
            engine._execute_python_workflow(_FailingWorkflow())
    finally:
        core_mod.logger.remove(handler)

    dumped = "".join(records)
    assert "失败前已产生的输出" in dumped
    assert "processed" in dumped and "4" in dumped


def test_logging_failure_does_not_mask_the_real_exception():
    """输出无法序列化时，日志降级为 repr，绝不能盖住原异常。"""
    class _Unserializable:
        def __repr__(self):
            return "<unserializable>"

    class _Workflow(_StoppingWorkflow):
        def run(self):
            self.output["obj"] = _Unserializable()
            raise ValueError("boom")

    engine = make_engine()
    with pytest.raises(WorkflowExecutionError) as caught:
        engine._execute_python_workflow(_Workflow())
    assert isinstance(caught.value.__cause__, ValueError)
