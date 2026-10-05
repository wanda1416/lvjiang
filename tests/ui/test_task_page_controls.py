"""任务页只能控制当前目标上由本页启动的任务，避免跨页误停。"""
from types import SimpleNamespace

from PyQt6.QtWidgets import QPushButton

from lvjiang.apps.yysls.ui.tuning.tuning_tab import TuningTab
from lvjiang.ui.batch.batch_tab import BatchTab
from lvjiang.ui.hotkeys import hotkey_label
from lvjiang.ui.main.run_control import RunControlMixin


def test_task_pages_disable_foreign_run_controls_and_restore_after_finish(qapp):
    host = SimpleNamespace(
        _user_config=SimpleNamespace(hotkeys=SimpleNamespace(
            start="F9", stop="F10", pause="F11")),
        _current_run_context=None, _run_state="idle", _running=False,
        btn_run_workflow=QPushButton(), btn_pause_resume=QPushButton(),
        _hotkey_label=hotkey_label, _backend_ready=lambda: True,
        _plan_allows_backend=lambda: True, start_denied_label=lambda: "",
        automation_state_changed=SimpleNamespace(emit=lambda state: None),
        _emit_concurrency_changed=lambda: None,
        _sync_projected_context_locks=lambda: None,
        findChildren=lambda cls: [],
    )
    batch = SimpleNamespace(
        _host=host, _btn_run=QPushButton(), _btn_pause_resume=QPushButton())
    tuning = SimpleNamespace(
        _host=host, btn_run_tuning=QPushButton(),
        btn_pause_resume=QPushButton(), _find_progress_widget=lambda: None)
    host._refresh_pause_button = lambda: RunControlMixin._refresh_pause_button(host)
    buttons = (
        (host.btn_run_workflow, host.btn_pause_resume),
        (batch._btn_run, batch._btn_pause_resume),
        (tuning.btn_run_tuning, tuning.btn_pause_resume),
    )
    clicks = []
    for start, pause in buttons:
        start.clicked.connect(lambda: clicks.append("stop"))
        pause.clicked.connect(lambda: clicks.append("pause"))

    def refresh(scope, state, owner):
        host._current_run_context = (
            SimpleNamespace(name="测试任务", metadata={"execution_scope": scope})
            if scope else None)
        host._running = bool(scope)
        host._run_state = state
        RunControlMixin._refresh_run_button(host)
        BatchTab._refresh_run_button(batch, state)
        TuningTab._on_automation_state(tuning, state)
        for index, (start, pause) in enumerate(buttons):
            if scope and index != owner:
                assert start.text() == "测试任务运行中"
                assert not start.isEnabled() and not pause.isEnabled()
                start.click()
                pause.click()
                assert not clicks
            elif scope:
                assert "停止" in start.text()
                assert start.isEnabled() == (state != "stopping")
                assert pause.isEnabled() == (state in ("running", "paused"))
            else:
                assert start.isEnabled() and not pause.isEnabled()

    refresh("auto_tuning", "running", 2)
    refresh("auto_tuning", "paused", 2)
    refresh("auto_tuning", "stopping", 2)
    refresh("batch", "running", 1)
    refresh("daily", "running", 0)
    refresh("auto_gather", "running", -1)
    refresh("", "idle", -1)
