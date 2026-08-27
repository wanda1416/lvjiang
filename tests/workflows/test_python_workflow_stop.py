"""Python 工作流停止信号的引擎边界测试。"""

from lvjiang.workflows.engine import WorkflowEngine
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
