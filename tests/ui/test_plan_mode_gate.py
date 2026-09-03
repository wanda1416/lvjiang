"""方案的连接模式校验：按钮第四态、两个拦截点、订阅方分支。"""

from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QPushButton

from lvjiang.core.config.plans import (
    PLAN_MODE_ADB,
    PLAN_MODE_WINDOW,
    Plan,
    save_plans,
    set_active_plan_id,
)
from lvjiang.ui.main.run_control import (
    PLAN_CUSTOM_LABEL,
    STATE_PLAN_UNSUPPORTED,
    RunControlMixin,
)
from lvjiang.ui.main.window import _ContextComboBox


class _Log:
    def __init__(self):
        self.lines = []

    def append(self, text):
        self.lines.append(text)


class _StatusBar:
    def __init__(self):
        self.messages = []

    def showMessage(self, text, *args):  # noqa: N802
        self.messages.append(text)


class _GateHost(RunControlMixin):
    def __init__(self, qtbot, *, backend, plan=None):
        self.plan_combo = _ContextComboBox()
        qtbot.addWidget(self.plan_combo)
        self.plan_combo.addItem(PLAN_CUSTOM_LABEL, "")
        if plan is not None:
            save_plans([plan])
            set_active_plan_id(plan.id)
            self.plan_combo.addItem(plan.name, plan.id)
            self.plan_combo.setCurrentIndex(1)
        self._backend = backend
        self._device_ready = True
        self._target_window = object()
        self._run_state = "idle"
        self.log_text = _Log()
        self._status_bar = _StatusBar()
        self.btn_run_workflow = QPushButton()
        qtbot.addWidget(self.btn_run_workflow)
        self._user_config = SimpleNamespace(
            hotkeys=SimpleNamespace(start="F9", stop="F10", pause="F11"))
        self.states = []
        self.automation_state_changed = SimpleNamespace(
            emit=self.states.append)
        self.ran = []
        self._left_tabs = None

    def statusBar(self):  # noqa: N802
        return self._status_bar

    def findChildren(self, _type):
        return []

    def _on_run_workflow(self):
        self.ran.append(True)


def _window_plan() -> Plan:
    return Plan.create("端游", modes=[PLAN_MODE_WINDOW])


def test_allows_everything_without_a_plan(qtbot):
    host = _GateHost(qtbot, backend=PLAN_MODE_ADB)

    assert host._plan_allows_backend()


def test_rejects_backend_the_plan_does_not_declare(qtbot):
    host = _GateHost(qtbot, backend=PLAN_MODE_ADB, plan=_window_plan())

    assert not host._plan_allows_backend()


def test_accepts_declared_backend(qtbot):
    host = _GateHost(qtbot, backend=PLAN_MODE_WINDOW, plan=_window_plan())

    assert host._plan_allows_backend()


def test_run_button_shows_the_fourth_state(qtbot):
    host = _GateHost(qtbot, backend=PLAN_MODE_ADB, plan=_window_plan())

    host._refresh_run_button()

    assert host.states == [STATE_PLAN_UNSUPPORTED]
    assert host.btn_run_workflow.text() == "方案不支持"
    assert "#9E9E9E" in host.btn_run_workflow.styleSheet()


def test_run_button_stays_clickable_so_the_hint_can_fire(qtbot):
    """置灰是样式；真禁用了就收不到点击，也就没法在左下角提示。"""
    host = _GateHost(qtbot, backend=PLAN_MODE_ADB, plan=_window_plan())

    host._refresh_run_button()

    assert host.btn_run_workflow.isEnabled()


def test_not_ready_wins_over_plan_state(qtbot):
    """还没连上时先说「未连接」，方案支不支持还谈不上。"""
    host = _GateHost(qtbot, backend=PLAN_MODE_ADB, plan=_window_plan())
    host._device_ready = False

    host._refresh_run_button()

    assert host.states == ["not_ready"]


@pytest.mark.parametrize("entry", ["_on_start", "_on_run_workflow_guard"])
def test_both_entry_points_are_blocked(qtbot, entry):
    """灰按钮只挡主界面点击；F9 和托盘走 _on_start，必须一起挡。"""
    host = _GateHost(qtbot, backend=PLAN_MODE_ADB, plan=_window_plan())

    if entry == "_on_start":
        host._on_start()
        assert host.ran == []
    else:
        RunControlMixin._on_run_workflow(host)
        assert host.ran == []

    assert host._status_bar.messages
    assert "不支持" in host._status_bar.messages[-1]
    assert any("不支持" in line for line in host.log_text.lines)


def test_start_proceeds_when_the_plan_allows_it(qtbot):
    host = _GateHost(qtbot, backend=PLAN_MODE_WINDOW, plan=_window_plan())

    host._on_start()

    assert host.ran == [True]
    assert host._status_bar.messages == []


def test_notice_names_the_plan_and_the_mode(qtbot):
    host = _GateHost(qtbot, backend=PLAN_MODE_ADB, plan=_window_plan())

    host._notify_plan_unsupported()

    message = host._status_bar.messages[-1]
    assert "端游" in message
    assert "ADB" in message
