"""Shared toolbar-button styling and dialog action-layout tests."""

from types import SimpleNamespace

from PyQt6.QtWidgets import QPushButton

from lvjiang.ui.button_styles import (
    ACTION_BUTTON_STYLE,
    DANGER_BUTTON_STYLE,
    NEUTRAL_BUTTON_STYLE,
    apply_button_style,
)
from lvjiang.ui.script_config_dialog import ScriptConfigDialog


def test_shared_button_styles_keep_geometry_consistent(qtbot):
    action = QPushButton("保存")
    neutral = QPushButton("取消")
    danger = QPushButton("删除")
    for button in (action, neutral, danger):
        qtbot.addWidget(button)

    apply_button_style(action)
    apply_button_style(neutral, variant="neutral")
    apply_button_style(danger, variant="danger")

    assert action.styleSheet() == ACTION_BUTTON_STYLE
    assert neutral.styleSheet() == NEUTRAL_BUTTON_STYLE
    assert danger.styleSheet() == DANGER_BUTTON_STYLE
    for button in (action, neutral, danger):
        assert "border-radius: 5px" in button.styleSheet()
        assert "padding: 5px 11px" in button.styleSheet()


def test_script_config_actions_share_one_left_right_row(qtbot, monkeypatch):
    monkeypatch.setattr(
        "lvjiang.ui.script_config_dialog.discover_scripts", lambda: []
    )
    monkeypatch.setattr(
        "lvjiang.ui.script_config_dialog.load_preferences",
        lambda: SimpleNamespace(order=[], visible={}, scopes={}, names={}),
    )
    dialog = ScriptConfigDialog(None)
    qtbot.addWidget(dialog)

    bottom = dialog.layout().itemAt(dialog.layout().count() - 1).layout()

    assert bottom.itemAt(0).widget() is dialog._btn_up
    assert bottom.itemAt(1).widget() is dialog._btn_down
    assert bottom.itemAt(2).spacerItem() is not None
    assert bottom.itemAt(3).widget() is dialog._btn_save
    assert bottom.itemAt(4).widget() is dialog._btn_cancel
