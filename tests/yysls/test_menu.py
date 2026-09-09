from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QMainWindow

from lvjiang.apps.yysls.ui import menus


@pytest.mark.parametrize("dev_mode", [False, True])
def test_attribute_config_is_dev_only_without_shortcut(
        qtbot, monkeypatch, dev_mode):
    host = QMainWindow()
    qtbot.addWidget(host)
    monkeypatch.setattr(
        menus,
        "get_resolver",
        lambda: SimpleNamespace(is_dev_mode=lambda: dev_mode),
    )

    menus.build_menu(host, host.menuBar())

    actions = {action.text(): action for action in host.menuBar().actions()[0].menu().actions()}
    assert actions["游戏配置"].shortcut().toString() == "F5"
    assert actions["调律配置"].shortcut().toString() == "F6"
    if dev_mode:
        assert "属性配置" in actions
        assert actions["属性配置"].shortcut().isEmpty()
    else:
        assert "属性配置" not in actions
