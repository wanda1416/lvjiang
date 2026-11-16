"""菜单快捷键：默认值、覆盖与解除绑定。"""
from __future__ import annotations

from PyQt6.QtGui import QAction

from lvjiang.ui.menu_shortcuts import (
    CORE_MENU_ITEMS,
    apply_shortcuts,
    current_shortcuts,
    resolved,
)


def test_defaults_match_the_zone_allocation():
    """默认键位：通用 F1~F4 + Alt+M，工具一律 Alt+字母。"""
    defaults = {entry.item_id: entry.default for entry in CORE_MENU_ITEMS}

    assert defaults["account_mgmt"] == "F1"
    assert defaults["user_mgmt"] == "F2"
    assert defaults["scene_editor"] == "F3"
    assert defaults["reference_mgr"] == "F4"
    assert defaults["map_mgr"] == "Alt+M"
    assert defaults["ocr"] == "Alt+I"
    assert defaults["mobile_device"] == "Alt+D"
    # 同一来源内不能有两个菜单项抢同一个键。
    assert len(set(defaults.values())) == len(defaults)


def test_override_and_unbind_are_applied_to_the_action(qtbot):
    action = QAction("任务历史")
    actions = {"task_history": action}
    apply_shortcuts(actions, {"task_history": "Alt+F2"})
    assert action.shortcut().toString() == "Alt+F2"

    apply_shortcuts(actions, {"task_history": ""})
    assert action.shortcut().isEmpty()


def test_resolved_falls_back_to_default_for_unknown_keys():
    assert resolved("task_params", {}) == "Alt+P"
    assert resolved("task_params", {"task_params": "Alt+Q"}) == "Alt+Q"
    assert current_shortcuts({})["task_params"] == "Alt+P"
