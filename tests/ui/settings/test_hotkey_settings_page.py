"""热键设置页：全局热键两列控件 + 菜单快捷键整表校验。"""
from __future__ import annotations

import pytest


@pytest.fixture
def dialog(qtbot):
    from lvjiang.ui.settings.dialog import SettingsDialog

    widget = SettingsDialog(None)
    qtbot.addWidget(widget)
    # 热键页在 __init__ 里已作为 Tab 构建，控件连同父级一起存活；不要重复构建，
    # 第二次构建出来的控件没有父级，会被 GC 掉。
    return widget


def test_hotkey_tab_has_both_groups(dialog):
    """五个全局热键（修饰键 + 主键）与通用/工具共 11 个菜单项。"""
    assert len(dialog._hotkey_combos) == 5
    assert len(dialog._menu_shortcut_combos) == 11
    for modifier_combo, key_combo in dialog._hotkey_combos.values():
        # 主键只开放 F8~F12：F1~F7 归菜单与插件，系统热键不接手。
        assert [key_combo.itemText(i) for i in range(key_combo.count())] == [
            f"F{i}" for i in range(8, 13)]
        assert modifier_combo.itemData(0) == ""


def test_hotkey_composition_keeps_modifier_optional(dialog):
    modifier_combo, key_combo = dialog._hotkey_combos["start"]
    modifier_combo.setCurrentIndex(modifier_combo.findData(""))
    assert dialog._compose_hotkey(modifier_combo, key_combo) == key_combo.currentText()
    modifier_combo.setCurrentIndex(modifier_combo.findData("Ctrl"))
    assert dialog._compose_hotkey(modifier_combo, key_combo).startswith("Ctrl+")


def test_menu_shortcut_conflict_is_reported(dialog):
    """两个菜单项抢同一个键位时不许保存。"""
    first, second = list(dialog._menu_shortcut_combos)[:2]
    dialog._menu_shortcut_combos[first].setCurrentText("Alt+H")
    dialog._menu_shortcut_combos[second].setCurrentText("Alt+H")

    problems = dialog._menu_shortcut_problems({})

    assert problems
    assert "Alt+H" in problems[0]


def test_menu_shortcut_colliding_with_global_hotkey_is_reported(dialog):
    """菜单键位与全局热键撞车同样拦下（含跨来源查重）。"""
    first = list(dialog._menu_shortcut_combos)[0]
    dialog._menu_shortcut_combos[first].setCurrentText("Alt+H")

    problems = dialog._menu_shortcut_problems({"start": "Alt+H"})

    assert problems
def test_hotkey_tab_uses_aligned_two_column_grid_and_overview_settings_tab(dialog):
    """全局热键 3×2、菜单快捷键每行 2 列且与全局热键第二列对齐；字体设置改为总览设置 1×2。"""
    grid = dialog._hotkey_grid
    hk_positions = []
    for modifier_combo, _key_combo in dialog._hotkey_combos.values():
        idx = grid.indexOf(modifier_combo.parentWidget())
        row, col, _rs, _cs = grid.getItemPosition(idx)
        hk_positions.append((row, col))
    assert len({r for r, _ in hk_positions}) == 3
    assert {c for _, c in hk_positions} == {1, 4}

    menu_positions = []
    for combo in dialog._menu_shortcut_combos.values():
        idx = grid.indexOf(combo)
        row, col, _rs, _cs = grid.getItemPosition(idx)
        menu_positions.append((row, col))
    assert len({r for r, _ in menu_positions}) == 6
    assert {c for _, c in menu_positions} == {1, 4}

    from PyQt6.QtWidgets import QGroupBox

    group_titles = [gb.title() for gb in dialog.findChildren(QGroupBox)]
    assert "总览设置" in group_titles
    assert "字体设置" not in group_titles
    assert dialog._overview_font_spin.parentWidget() is dialog._user_info_font_spin.parentWidget()
