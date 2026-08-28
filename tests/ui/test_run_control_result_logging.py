"""Workflow completion result logging policy tests."""

from lvjiang.ui.main import run_control


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
