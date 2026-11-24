"""插件页签的构造期弹窗回归：GameConfigDialog 不得在初始化时弹出独立窗口。

用例依赖 yysls 插件，按 tests/test_app_dependency_gate.py 的测试分层放在插件
测试目录下；顶层 Show 事件探针复用框架侧的 _TopLevelShowSpy。
"""

from __future__ import annotations

from PyQt6.QtWidgets import QApplication

from tests.ui.test_no_stray_popup_on_init import _TopLevelShowSpy


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
