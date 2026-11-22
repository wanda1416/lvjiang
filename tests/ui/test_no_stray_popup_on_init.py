"""回归测试：子控件在构造期不得在无父窗口时触发独立顶层弹窗。"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget


class _TopLevelShowSpy(QObject):
    """记录构造期间发出的顶层窗口 Show 事件。"""

    def __init__(self) -> None:
        super().__init__()
        self.shown_windows: list[str] = []

    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        if (
            event is not None
            and event.type() == QEvent.Type.Show
            and isinstance(watched, QWidget)
            and watched.isWindow()
        ):
            self.shown_windows.append(type(watched).__name__)
        return False


def test_alert_panel_does_not_popup_on_init_with_pending_alert(
    qtbot, monkeypatch,
) -> None:
    from lvjiang.ui.execution import alert_panel as mod
    from lvjiang.ui.execution.alert_panel import AlertPanel

    app = QApplication.instance()
    assert app is not None
    spy = _TopLevelShowSpy()
    app.installEventFilter(spy)
    try:
        monkeypatch.setattr(
            mod, "get_alerts", lambda: [{"id": "a1", "message": "存在未关闭告警"}],
        )
        orphan = AlertPanel()
        qtbot.addWidget(orphan)
        assert spy.shown_windows == []

        host = QWidget()
        qtbot.addWidget(host)
        QVBoxLayout(host).addWidget(orphan)
        host.show()
        assert orphan.isVisible()
        assert spy.shown_windows == ["QWidget"]

        spy.shown_windows.clear()
        parent_host = QWidget()
        qtbot.addWidget(parent_host)
        child_panel = AlertPanel(parent_host)
        assert spy.shown_windows == []
        assert not child_panel.isHidden()

        monkeypatch.setattr(mod, "get_alerts", list)
        empty_host = QWidget()
        qtbot.addWidget(empty_host)
        empty_panel = AlertPanel(empty_host)
        QVBoxLayout(empty_host).addWidget(empty_panel)
        empty_host.show()
        assert not empty_panel.isVisible()
    finally:
        app.removeEventFilter(spy)


def test_game_config_dialog_does_not_popup_stray_window_on_init(qtbot) -> None:
    from lvjiang.apps.yysls.ui.game_settings import GameConfigDialog

    app = QApplication.instance()
    assert app is not None
    spy = _TopLevelShowSpy()
    app.installEventFilter(spy)
    try:
        dialog = GameConfigDialog()
        qtbot.addWidget(dialog)
        assert spy.shown_windows == []
    finally:
        app.removeEventFilter(spy)
