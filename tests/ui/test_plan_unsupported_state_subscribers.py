"""automation_state_changed 的订阅方必须显式处理第四态。

三个订阅方原本都用 else / dict.get 兜底，未知状态会被当成「就绪」——
托盘的「开始」还亮着，调律 Tab 的 else 分支还会 mark_done() 误报完成。
"""

from types import SimpleNamespace

from PyQt6.QtWidgets import QPushButton

from lvjiang.ui.main.run_control import STATE_PLAN_UNSUPPORTED


class _Action:
    def __init__(self):
        self.enabled = True
        self.text = ""

    def setEnabled(self, value):  # noqa: N802
        self.enabled = value

    def setText(self, value):  # noqa: N802
        self.text = value


class _TrayIcon:
    def __init__(self):
        self.tooltip = ""

    def setIcon(self, _icon):  # noqa: N802
        pass

    def setToolTip(self, text):  # noqa: N802
        self.tooltip = text


def test_tray_disables_start_and_names_the_state(qtbot):
    from lvjiang.ui.main.tray_ops import TrayOpsMixin

    host = SimpleNamespace(
        _tray_icon=_TrayIcon(),
        _tray_action_start=_Action(),
        _tray_action_pause=_Action(),
        _tray_action_stop=_Action(),
        windowTitle=lambda: "律匠",
    )

    TrayOpsMixin._refresh_tray_icon(host, STATE_PLAN_UNSUPPORTED)

    assert host._tray_action_start.enabled is False
    assert "方案不支持" in host._tray_icon.tooltip


def test_tray_start_still_enabled_when_idle(qtbot):
    from lvjiang.ui.main.tray_ops import TrayOpsMixin

    host = SimpleNamespace(
        _tray_icon=_TrayIcon(),
        _tray_action_start=_Action(),
        _tray_action_pause=_Action(),
        _tray_action_stop=_Action(),
        windowTitle=lambda: "律匠",
    )

    TrayOpsMixin._refresh_tray_icon(host, "idle")

    assert host._tray_action_start.enabled is True


def test_batch_button_does_not_fall_through_to_ready(qtbot):
    from lvjiang.ui.batch.batch_tab import BatchTab

    button = QPushButton()
    pause = QPushButton()
    qtbot.addWidget(button)
    qtbot.addWidget(pause)
    host = SimpleNamespace(
        _btn_run=button,
        _btn_pause_resume=pause,
        _host=SimpleNamespace(_user_config=SimpleNamespace(
            hotkeys=SimpleNamespace(start="F9", stop="F10", pause="F11"))),
        _set_config_enabled=lambda _value: None,
    )

    BatchTab._refresh_run_button(host, STATE_PLAN_UNSUPPORTED)

    assert button.text() == "方案不支持"
    assert "#9E9E9E" in button.styleSheet()


class _ProgressWidget:
    def __init__(self):
        self.done = 0

    def mark_done(self):
        self.done += 1

    def set_paused(self, _value):
        pass


def _tuning_host(qtbot):
    button = QPushButton()
    pause = QPushButton()
    qtbot.addWidget(button)
    qtbot.addWidget(pause)
    progress = _ProgressWidget()
    host = SimpleNamespace(
        btn_run_tuning=button,
        btn_pause_resume=pause,
        _host=SimpleNamespace(
            _user_config=SimpleNamespace(hotkeys=SimpleNamespace(
                start="F9", stop="F10", pause="F11")),
            # 有引擎且带进度中枢，else 分支才会走到 mark_done
            _current_engine=SimpleNamespace(_progress_hub=object()),
        ),
        _find_progress_widget=lambda: progress,
        progress=progress,
    )
    return host


def test_tuning_button_does_not_fall_through_and_never_marks_done(qtbot):
    from lvjiang.apps.yysls.ui.tuning.tuning_tab import TuningTab

    host = _tuning_host(qtbot)

    TuningTab._on_automation_state(host, STATE_PLAN_UNSUPPORTED)

    assert host.btn_run_tuning.text() == "方案不支持"
    assert "#9E9E9E" in host.btn_run_tuning.styleSheet()
    assert host.progress.done == 0


def test_tuning_idle_still_marks_done(qtbot):
    """反证：上一条不是因为 mark_done 根本不会触发才通过的。"""
    from lvjiang.apps.yysls.ui.tuning.tuning_tab import TuningTab

    host = _tuning_host(qtbot)

    TuningTab._on_automation_state(host, "idle")

    assert host.progress.done == 1
