"""Shared toolbar-button styling and dialog action-layout tests."""

from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QCheckBox, QPushButton

from lvjiang.ui.button_styles import (
    ACTION_BUTTON_STYLE,
    DANGER_BUTTON_STYLE,
    NEUTRAL_BUTTON_STYLE,
    apply_button_style,
)
from lvjiang.ui.ocr.dialog import OCRDialog
from lvjiang.ui.scripts.config_dialog import ScriptConfigDialog
from lvjiang.ui.widgets import centered_cell_widget


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


def test_centered_cell_widget_centers_checkbox(qtbot):
    checkbox = QCheckBox()
    container = centered_cell_widget(checkbox)
    qtbot.addWidget(container)

    alignment = container.layout().itemAt(0).alignment()
    assert alignment & Qt.AlignmentFlag.AlignHorizontal_Mask
    assert alignment & Qt.AlignmentFlag.AlignVertical_Mask


def test_ocr_cleaning_rule_actions_use_shared_styles(qtbot):
    dialog = OCRDialog()
    qtbot.addWidget(dialog)

    for button in (
        dialog._btn_add_repl,
        dialog._btn_add_pattern,
        dialog._btn_test,
    ):
        assert button.styleSheet() == ACTION_BUTTON_STYLE
    for button in (dialog._btn_del_repl, dialog._btn_del_pattern):
        assert button.styleSheet() == DANGER_BUTTON_STYLE


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


# ─── 用户总览 / 数据模型对话框 ────────────────────────────────
# 这两处此前漏了统一样式，是系统默认灰按钮，跟同页其他按钮不一样。
# 断言写成「与同页参照按钮一致」而不是硬编码某个 variant，这样以后调色板
# 换了也不用改测试，跑偏了照样能抓到。

def _profile_overview(qtbot):
    from unittest.mock import MagicMock

    from lvjiang.ui.profile.tab import ProfileTab

    tab = ProfileTab(MagicMock())
    qtbot.addWidget(tab)
    return tab


def test_profile_overview_metadata_button_matches_siblings(qtbot):
    """「数据模型」要和同页的新建/重命名/删除分组长得一样。"""
    from lvjiang.ui.user_toolbar import USER_ACTION_BTN_STYLE

    tab = _profile_overview(qtbot)
    buttons = {b.text(): b for b in tab.findChildren(QPushButton)}
    assert "数据模型" in buttons, "用户总览页应有「数据模型」按钮"

    sibling = buttons["新建分组"]
    assert sibling.styleSheet() == USER_ACTION_BTN_STYLE, "参照按钮本身应当已统一"
    assert buttons["数据模型"].styleSheet() == USER_ACTION_BTN_STYLE
    assert buttons["数据模型"].minimumHeight() == sibling.minimumHeight()


def _known_variant(style: str) -> bool:
    return style in (ACTION_BUTTON_STYLE, NEUTRAL_BUTTON_STYLE, DANGER_BUTTON_STYLE)


def test_profile_definition_dialog_buttons_all_styled(qtbot):
    """数据模型对话框里每个按钮都得用统一样式，且不能被固定宽度截断。"""
    from lvjiang.ui.profile.settings_dialog import ProfileDefinitionDialog

    dialog = ProfileDefinitionDialog()
    qtbot.addWidget(dialog)

    buttons = dialog.findChildren(QPushButton)
    assert buttons, "对话框里应当有按钮"
    unstyled = [b.text() for b in buttons if not _known_variant(b.styleSheet())]
    assert not unstyled, f"这些按钮仍是旧风格：{unstyled}"

    clipped = [
        b.text() for b in buttons
        if b.minimumWidth() == b.maximumWidth()      # 设了固定宽
        and b.minimumWidth() < b.sizeHint().width()  # 却装不下带内边距的文字
    ]
    assert not clipped, f"这些按钮的固定宽度装不下统一样式：{clipped}"


def test_sync_target_remove_button_styled_and_fits(qtbot):
    """行内「×」删除键：套 danger 样式后 30px 会挤掉字符，列宽与按钮宽需同步放大。"""
    from lvjiang.ui.profile.settings_dialog import _SyncTargetsWidget

    widget = _SyncTargetsWidget()
    qtbot.addWidget(widget)
    widget.add_row()

    removes = [b for b in widget.findChildren(QPushButton) if b.text() == "×"]
    assert len(removes) == 1
    btn = removes[0]
    assert btn.styleSheet() == DANGER_BUTTON_STYLE
    assert btn.minimumWidth() >= btn.sizeHint().width(), "固定宽度装不下「×」"
    assert widget._table.columnWidth(4) >= btn.minimumWidth(), "列宽装不下删除键"
