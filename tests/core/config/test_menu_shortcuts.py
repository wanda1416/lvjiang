"""菜单快捷键：区间归属、系统保留组合与整表冲突。

键位按「谁用」分段：F1~F4 归通用/工具菜单、F5~F7 归插件菜单、F8~F12 归
系统级全局热键。全局热键占用的裸键，窗口内 QAction 永远收不到，所以越区间
必须在保存前就拦住，而不是等用户发现"按了没反应"。
"""
from __future__ import annotations

import pytest

from lvjiang.core.config.menu_shortcuts import (
    ZONE_CORE_MENU,
    ZONE_GLOBAL_HOTKEY,
    ZONE_PLUGIN_MENU,
    cross_source_conflicts,
    normalize_menu_shortcut,
    validate_menu_shortcuts,
    zone_of,
)


def test_zone_of_splits_the_function_keys():
    assert zone_of("F1") == ZONE_CORE_MENU
    assert zone_of("Alt+F4") == ZONE_CORE_MENU
    assert zone_of("F6") == ZONE_PLUGIN_MENU
    assert zone_of("F9") == ZONE_GLOBAL_HOTKEY
    assert zone_of("Alt+M") is None
    assert zone_of("") is None


def test_core_menu_accepts_its_zone_and_letters():
    assert normalize_menu_shortcut("f1", owner=ZONE_CORE_MENU) == "F1"
    assert normalize_menu_shortcut("alt+m", owner=ZONE_CORE_MENU) == "Alt+M"
    assert normalize_menu_shortcut("Alt+F3", owner=ZONE_CORE_MENU) == "Alt+F3"
    assert normalize_menu_shortcut("", owner=ZONE_CORE_MENU) == ""


def test_plugin_menu_accepts_only_its_own_zone():
    assert normalize_menu_shortcut("F5", owner=ZONE_PLUGIN_MENU) == "F5"
    assert normalize_menu_shortcut("Alt+F7", owner=ZONE_PLUGIN_MENU) == "Alt+F7"
    assert normalize_menu_shortcut("Alt+Q", owner=ZONE_PLUGIN_MENU) == "Alt+Q"
    for raw in ("F1", "F8", "Alt+F1", "Alt+F9"):
        with pytest.raises(ValueError):
            normalize_menu_shortcut(raw, owner=ZONE_PLUGIN_MENU)


def test_reserved_combos_are_rejected():
    """Alt+A 是截图、Alt+F4 是关窗，绑上去只会变成"点了没反应"。"""
    for raw in ("Alt+A", "Alt+F4"):
        with pytest.raises(ValueError):
            normalize_menu_shortcut(raw, owner=ZONE_CORE_MENU)


def test_duplicate_shortcuts_inside_one_source_are_reported():
    problems = validate_menu_shortcuts(
        {"user_mgmt": "F2", "scene_editor": "F2"}, owner=ZONE_CORE_MENU)

    assert problems
    assert "F2" in problems[0]


def test_cross_source_conflicts_name_both_sides():
    problems = cross_source_conflicts({
        "core:任务历史": "Alt+H",
        "plugin:游戏配置": "Alt+H",
    })

    assert len(problems) == 1
    assert "core:任务历史" in problems[0]
    assert "plugin:游戏配置" in problems[0]
