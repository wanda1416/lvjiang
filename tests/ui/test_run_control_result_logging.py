"""Workflow completion result logging policy tests."""

import json
from types import SimpleNamespace

from lvjiang.ui.main import run_control


def test_auto_tuning_disables_engine_failure_output_dump():
    from lvjiang.apps.yysls.workflows.implementations.auto_tuning import (
        AutoTuningWorkflow,
    )

    assert AutoTuningWorkflow.LOG_PARTIAL_OUTPUT_ON_FAILURE is False


def test_auto_tuning_result_is_not_dumped_to_logs(monkeypatch):
    messages: list[str] = []
    monkeypatch.setattr(run_control.logger, "info", messages.append)

    logged = run_control._log_workflow_result(
        "auto_tuning",
        {"tuning_reports": [{"name": "测试装备", "rounds": 5}]},
        interrupted=False,
    )

    assert logged is False
    assert messages == []


def test_other_workflow_result_keeps_generic_log(monkeypatch):
    messages: list[str] = []
    monkeypatch.setattr(run_control.logger, "info", messages.append)

    logged = run_control._log_workflow_result(
        "daily_demo", {"value": "已完成"}, interrupted=True
    )

    assert logged is True
    assert len(messages) == 1
    assert "daily_demo" in messages[0]
    assert "用户中断，部分结果" in messages[0]
    assert "已完成" in messages[0]


def test_interrupted_empty_result_still_writes_history_json(tmp_path, monkeypatch):
    import lvjiang.constants as constants

    monkeypatch.setattr(constants, "OUTPUT_DIR", tmp_path / "output")

    class Log:
        def append(self, _message):
            pass

    host = SimpleNamespace(
        _current_engine=SimpleNamespace(run_username="测试用户"),
        log_text=Log(),
    )

    path = run_control.RunControlMixin._save_workflow_result(
        host, "daily_demo", {}, interrupted=True)

    assert path is not None
    assert path.name.endswith("_interrupted.json")
    assert json.loads(path.read_text(encoding="utf-8")) == {}
