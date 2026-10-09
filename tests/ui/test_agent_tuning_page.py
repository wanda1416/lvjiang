"""Changing the Agent UI context must not mix users' generated plans."""
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget

from lvjiang.apps.yysls.core.agent.service import AgentGrant
from lvjiang.apps.yysls.ui.tuning.agent_page import AgentTuningPage


class Host(QWidget):
    user_changed = pyqtSignal(str)


def test_page_binds_user_and_target_and_disables_ungranted_execution(qtbot, tmp_path, monkeypatch):
    from lvjiang import constants
    from lvjiang.apps.yysls.ui.tuning import agent_page
    licensed = [True]
    monkeypatch.setattr(agent_page, "has_agent_access", lambda: licensed[0])
    monkeypatch.setattr(constants, "PROJECT_ROOT", tmp_path)
    host = Host()
    active = ["user_a"]
    manager = SimpleNamespace(list_users=lambda: ["user_a", "user_b"],
                              get_active_user_name=lambda: active[0])
    host.user_manager = manager
    target = SimpleNamespace(id="target_a", display_name="执行目标", kind="window", ready=True)
    host._execution_targets = SimpleNamespace(all=lambda: [target], active_target_id=target.id)
    page = AgentTuningPage(host)
    qtbot.addWidget(host)
    qtbot.addWidget(page)
    assert page.service.grant.user == "user_a"
    assert page.service.grant.target_id == "target_a"
    active[0] = "user_b"
    host.user_changed.emit("user_b")
    assert page.service.grant.user == "user_a"  # Main editor changes cannot broaden authorization.
    page._records = {"example": {"id": "example", "name": "测试方案", "goal": "培养首饰",
                                 "suggestions": [], "run_config": {"selected_slots": ["ring"], "rules": {}}}}
    page.results.addItem("测试方案", "example")
    assert not page.start.isEnabled()
    page.permissions["execute"].setChecked(True)
    assert page.start.isEnabled()
    licensed[0] = False
    page._show_result()
    assert not page.start.isEnabled()
    assert not page.toggle.isEnabled()
    assert "Lv1" in page.entitlement.text()
    monkeypatch.setattr(page.service, "validate_auto_tuning", lambda *_args: pytest.fail("Unlicensed start"))
    page._start()
    assert "Lv1" in page.status.text()
    licensed[0] = True
    page.user.combo.setCurrentIndex(page.user.combo.findData("user_b"))
    assert page.service.grant.user == "user_b"
    page._refresh_results()
    assert not page.start.isEnabled()
    page._shutdown()
    assert page.service.grant == AgentGrant(read_data=False)


@pytest.mark.parametrize("stopped, expected", [(False, "failed"), (True, "interrupted")])
def test_scan_failure_and_user_stop_have_distinct_terminal_states(stopped, expected):
    import threading

    from lvjiang.ui.main.run_control import RunControlMixin, WorkflowWorker

    worker = WorkflowWorker("scan_all_loadouts", lambda: None)
    worker.result_or_exception = {}
    stop = threading.Event()
    if stopped:
        stop.set()
    run = SimpleNamespace(worker=worker, engine=SimpleNamespace(return_value=-1),
                          stop_event=stop, metadata={})
    saved = []
    host = SimpleNamespace(
        _run_manager=SimpleNamespace(run=lambda _id: run), log_text=[],
        _save_workflow_result=lambda *_args, **_kwargs: None,
        _finish_task_run=lambda *_args, **kwargs: saved.append(kwargs["status"]),
        _auto_save_session=lambda *_args: pytest.fail("Neither path may save a partial session"),
    )
    RunControlMixin._on_workflow_finished.__wrapped__(host, "test_run")
    assert run.metadata["terminal_state"] == expected
    assert saved == [expected]
