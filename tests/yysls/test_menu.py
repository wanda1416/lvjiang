from PyQt6.QtWidgets import QMainWindow

from lvjiang.apps.yysls.ui import menus


def test_menu_entries_are_available_to_everyone(qtbot):
    """属性配置对所有用户开放，不再只在开发者模式下出现。"""
    host = QMainWindow()
    qtbot.addWidget(host)

    menus.build_menu(host, host.menuBar())

    assert host.menuBar().actions()[0].data() == "yysls"
    actions = {action.text(): action for action in host.menuBar().actions()[0].menu().actions()}
    assert actions["游戏配置"].shortcut().toString() == "F5"
    assert actions["调律配置"].shortcut().toString() == "F6"
    assert actions["属性配置"].shortcut().isEmpty()
    assert "采集录制" not in actions
