"""Shared toolbar-button styling and dialog action-layout tests."""

from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QPushButton

from lvjiang.ui.button_styles import (
    ACTION_BUTTON_STYLE,
    DANGER_BUTTON_STYLE,
    NEUTRAL_BUTTON_STYLE,
    apply_button_style,
)
from lvjiang.ui.scripts.config_dialog import ScriptConfigDialog


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
        "lvjiang.ui.scripts.config_dialog.discover_scripts", lambda: []
    )
    monkeypatch.setattr(
        "lvjiang.ui.scripts.config_dialog.load_preferences",
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


def test_script_config_row_move_rebuilds_owned_cell_widgets(qtbot, monkeypatch):
    scripts = [
        {
            "id": "first", "name": "脚本甲", "scope": "daily",
            "hidden": False, "wf_file": "first.wf", "parameters": [],
        },
        {
            "id": "second", "name": "脚本乙", "scope": "dedicated",
            "hidden": True, "wf_file": "second.wf", "parameters": [],
        },
    ]
    monkeypatch.setattr(
        "lvjiang.ui.scripts.config_dialog.discover_scripts", lambda: scripts)
    monkeypatch.setattr(
        "lvjiang.ui.scripts.config_dialog.load_preferences",
        lambda: SimpleNamespace(
            order=["first", "second"], visible={}, scopes={}, names={}),
    )
    dialog = ScriptConfigDialog(None)
    qtbot.addWidget(dialog)
    table = dialog._table

    old_combos = [
        table.cellWidget(row, dialog.COL_SCOPE)
        for row in range(table.rowCount())
    ]
    table.item(0, dialog.COL_NAME).setText("自定义甲")
    table.item(0, dialog.COL_EXPOSE).setCheckState(Qt.CheckState.Unchecked)
    table.setCurrentCell(0, dialog.COL_NAME)
    dialog._move_row(1)

    assert table.item(1, dialog.COL_NAME).text() == "自定义甲"
    assert table.item(1, dialog.COL_NAME).data(Qt.ItemDataRole.UserRole) == "first"
    assert table.item(1, dialog.COL_EXPOSE).checkState() == Qt.CheckState.Unchecked
    assert table.cellWidget(1, dialog.COL_SCOPE).currentData() == "daily"
    assert all(
        table.cellWidget(row, dialog.COL_SCOPE) not in old_combos
        for row in range(table.rowCount())
    )

    # 连续移动覆盖真实用户快速点击上移/下移的路径。
    for _ in range(50):
        dialog._move_row(-1)
        dialog._move_row(1)
    assert table.currentRow() == 1
