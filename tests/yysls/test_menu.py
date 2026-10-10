from PyQt6.QtWidgets import QMainWindow

from lvjiang.apps.yysls.ui import menus


def test_menu_entries_are_available_to_everyone(qtbot, tmp_path, monkeypatch):
    """属性配置对所有用户开放，不再只在开发者模式下出现。"""
    from lvjiang import constants
    from lvjiang.core import agent_connection
    monkeypatch.setattr(constants, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(agent_connection, "settings_path", lambda _root: tmp_path / "connection.json")
    host = QMainWindow()
    qtbot.addWidget(host)

    menus.build_menu(host, host.menuBar())

    assert host.menuBar().actions()[0].data() == "yysls"
    actions = {action.text(): action for action in host.menuBar().actions()[0].menu().actions()}
    assert actions["游戏配置"].shortcut().toString() == "F5"
    assert actions["调律配置"].shortcut().toString() == "F6"
    assert actions["属性配置"].shortcut().isEmpty()
    assert "采集录制" not in actions
    actions["智能调律"].trigger()
    assert host._agent_tuning_dialog.isVisible()
    assert not host._agent_tuning_dialog.isModal()
    host._agent_tuning_dialog.close()
