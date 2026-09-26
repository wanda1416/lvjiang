"""暂停和结束请求必须先给反馈，再等待工作线程确认。"""

from types import SimpleNamespace

from PyQt6.QtWidgets import QPushButton, QWidget

from lvjiang.core.config.plans import Plan
from lvjiang.ui.main.run_control import (
    STATE_PAUSING,
    STATE_STOPPING,
    RunControlMixin,
    _AcknowledgedPauseEvent,
)


class _Signal:
    def __init__(self):
        self.values = []

    def emit(self, value):
        self.values.append(value)


class _Log:
    def __init__(self):
        self.values = []

    def append(self, value):
        self.values.append(value)


class _StatusBar:
    def __init__(self):
        self.message = ""

    def showMessage(self, message):  # noqa: N802 - Qt API shape
        self.message = message


class Host(QWidget, RunControlMixin):
    def __init__(self):
        super().__init__()
        self._run_state = "running"
        self._stop_requested = False
        self._current_worker = object()
        self._user_config = SimpleNamespace(
            hotkeys=SimpleNamespace(start="F9", stop="F10", pause="F11"),
        )
        self.btn_run_workflow = QPushButton(self)
        self.btn_pause_resume = QPushButton(self)
        self.automation_state_changed = _Signal()
        self.log_text = _Log()
        self._status_bar = _StatusBar()
        self._pause_event = _AcknowledgedPauseEvent(
            self._on_pause_acknowledged,
        )
        self._pause_event.set()

    @staticmethod
    def _hotkey_label(label, hotkey):
        return f"{label} ({hotkey})"

    @staticmethod
    def _hotkey_status(label, *items):
        return label

    def statusBar(self):  # noqa: N802 - Qt API shape
        return self._status_bar

    def _set_context_controls_locked(self, _reason, _locked):
        pass

    @staticmethod
    def _backend_ready():
        return True

    @staticmethod
    def _plan_allows_backend():
        return True


class AdminGateHost(RunControlMixin):
    def __init__(self):
        self._current_worker = None
        self._run_state = "idle"
        self._user_config = SimpleNamespace(
            hotkeys=SimpleNamespace(start="F9", stop="F10", pause="F11"),
        )
        self.plan = Plan.create("端游", requires_admin=True)
        self.messages = []
        self._status_bar = _StatusBar()

    def _selected_plan(self):
        return self.plan

    def _show_workflow_start_error(self, message):
        self.messages.append(message)

    def statusBar(self):  # noqa: N802 - Qt API shape
        return self._status_bar


def test_pause_button_stays_pending_until_worker_observes_event(qtbot):
    host = Host()
    qtbot.addWidget(host)

    host._request_pause()

    assert host._run_state == STATE_PAUSING
    assert host.btn_pause_resume.text() == "暂停中"
    assert not host.btn_pause_resume.isEnabled()
    assert host.automation_state_changed.values[-1] == STATE_PAUSING

    # 模拟工作线程执行到下一个暂停检查点。
    assert not host._pause_event.is_set()

    assert host._run_state == "paused"
    assert host.btn_pause_resume.text().startswith("恢复")
    assert host.btn_pause_resume.isEnabled()
    assert host.automation_state_changed.values[-1] == "paused"


def test_stop_button_stays_pending_until_worker_finishes(qtbot):
    host = Host()
    qtbot.addWidget(host)

    host._request_stop()

    assert host._run_state == STATE_STOPPING
    assert host._stop_requested
    assert host.btn_run_workflow.text() == "结束中"
    assert not host.btn_run_workflow.isEnabled()
    assert host.automation_state_changed.values[-1] == STATE_STOPPING

    host._end_automation("测试任务")

    assert host._run_state == "idle"
    assert not host._stop_requested
    assert host.btn_run_workflow.isEnabled()
    assert host.automation_state_changed.values[-1] == "idle"


def test_admin_plan_rejects_start_before_automation_state_changes(monkeypatch):
    from lvjiang.core import platforms

    monkeypatch.setattr(platforms, "IS_WINDOWS", True)
    monkeypatch.setattr(platforms, "is_process_elevated", lambda: False)
    host = AdminGateHost()

    assert not host._begin_automation("测试任务")
    assert host._run_state == "idle"
    assert "端游" in host.messages[0]
    assert "以管理员身份运行" in host.messages[0]
    assert host._status_bar.message == host.messages[0]


def test_admin_requirement_is_windows_only(monkeypatch):
    from lvjiang.core import platforms

    monkeypatch.setattr(platforms, "IS_WINDOWS", False)
    monkeypatch.setattr(
        platforms, "is_process_elevated",
        lambda: (_ for _ in ()).throw(AssertionError("不应检查")),
    )

    assert AdminGateHost()._plan_admin_requirement_error() == ""
