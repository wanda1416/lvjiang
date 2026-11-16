"""菜单快捷键：稳定 id、默认键位与运行期应用。

键位只在这里定义一次：菜单构建时按 id 把 ``QAction`` 收进 ``_menu_actions``，
设置页按同一份清单渲染编辑控件，保存后 :func:`apply_shortcuts` 直接改已存在的
动作——不需要重启窗口。

默认键位按区间分配（规则见 :mod:`lvjiang.core.config.menu_shortcuts`）：
通用菜单用 ``F1–F4`` 与 ``Alt+M``，工具菜单统一用 ``Alt+字母``；插件菜单
（F5–F7）由插件自己登记，不在这张表里。
"""

from __future__ import annotations

from PyQt6.QtGui import QAction, QKeySequence

from ..core.config.menu_shortcuts import ZONE_CORE_MENU, MenuItem

#: 通用菜单与工具菜单的键位清单。``label`` 是中文原文，渲染时再经 ``tr()``。
CORE_MENU_ITEMS: tuple[MenuItem, ...] = (
    MenuItem("account_mgmt", ZONE_CORE_MENU, "F1", group="通用", label="账户管理"),
    MenuItem("user_mgmt", ZONE_CORE_MENU, "F2", group="通用", label="用户管理"),
    MenuItem("scene_editor", ZONE_CORE_MENU, "F3", group="通用", label="场景管理"),
    MenuItem("reference_mgr", ZONE_CORE_MENU, "F4", group="通用", label="图库管理"),
    MenuItem("map_mgr", ZONE_CORE_MENU, "Alt+M", group="通用", label="地图管理"),
    MenuItem("ocr", ZONE_CORE_MENU, "Alt+I", group="工具", label="图像识别"),
    MenuItem("script_editor", ZONE_CORE_MENU, "Alt+S", group="工具", label="脚本编辑"),
    MenuItem("batch_settings", ZONE_CORE_MENU, "Alt+B", group="工具", label="批量配置"),
    MenuItem("task_params", ZONE_CORE_MENU, "Alt+P", group="工具", label="任务参数"),
    MenuItem("task_history", ZONE_CORE_MENU, "Alt+H", group="工具", label="任务历史"),
    MenuItem("mobile_device", ZONE_CORE_MENU, "Alt+D", group="工具", label="移动设备"),
)

_ITEMS: dict[str, MenuItem] = {item.item_id: item for item in CORE_MENU_ITEMS}


def item(item_id: str) -> MenuItem:
    return _ITEMS[item_id]


def resolved(item_id: str, overrides: dict[str, str]) -> str:
    """该菜单项的最终键位：用户覆盖优先，否则用默认值。"""
    spec = _ITEMS[item_id]
    return str((overrides or {}).get(item_id, spec.default) or "").strip()


def current_shortcuts(overrides: dict[str, str]) -> dict[str, str]:
    """当前生效的整张表：{item_id: 键位}。"""
    return {item_id: resolved(item_id, overrides) for item_id in _ITEMS}


def apply_shortcuts(actions: dict[str, QAction], overrides: dict[str, str]) -> None:
    """把最终键位写到动作上；空键位表示不绑定。"""
    for item_id, action in actions.items():
        if item_id not in _ITEMS:
            continue
        text = resolved(item_id, overrides)
        action.setShortcut(QKeySequence(text) if text else QKeySequence())
