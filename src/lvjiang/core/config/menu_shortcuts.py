"""菜单快捷键：键位区间归属、写法规范化与整表校验。

键位按「谁用」划分区间，跨区间即视为配置错误：

- ``F1–F4`` 与 ``Alt+F1–F4``：通用菜单（``core_menu``）
- ``F5–F7`` 与 ``Alt+F5–F7``：插件菜单（``plugin_menu``）
- ``F8–F12`` 与 ``Alt/Ctrl/Shift+F8–F12``：系统级全局热键，菜单不得占用
- ``Alt+字母``：通用菜单与插件菜单都可以用，因此必须做跨来源查重

为什么菜单不能用裸 ``F8–F12``：那几颗键由 ``RegisterHotKey`` 注册成系统热键，
操作系统先投递给它，窗口内的 ``QAction`` 永远抢不到——不是"优先级低"，是根本
收不到。

``Alt+A``（截图）与 ``Alt+F4``（关窗）等系统组合一律禁止。字母选英文首字母，
拼音会撞车（参数/配置都是 P）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: 区间归属：F 键编号 → 使用方
ZONE_CORE_MENU = "core_menu"
ZONE_PLUGIN_MENU = "plugin_menu"
ZONE_GLOBAL_HOTKEY = "global_hotkey"

_F_ZONES = {
    **{number: ZONE_CORE_MENU for number in range(1, 5)},
    **{number: ZONE_PLUGIN_MENU for number in range(5, 8)},
    **{number: ZONE_GLOBAL_HOTKEY for number in range(8, 13)},
}

#: 系统或常见软件已占用的组合，小写比较。
RESERVED_COMBOS = {
    "alt+a",          # 截图（微信/QQ 等）
    "alt+f4",         # 关闭窗口
    "alt+tab",        # 切换窗口
    "alt+esc",        # 循环窗口
    "alt+space",      # 窗口菜单
    "alt+enter",      # 全屏切换
    "alt+printscreen",
}

_LETTER = re.compile(r"^[A-Z]$")
_F_KEY = re.compile(r"^F([1-9]|1[0-2])$")


@dataclass(frozen=True)
class MenuItem:
    """一个可配置的菜单项：稳定 id 决定配置键，显示名由菜单自己给。

    ``group`` / ``label`` 是**中文原文**（渲染时再过 ``tr()``），只用于设置页；
    持久化与逻辑判断一律用 :attr:`item_id`。
    """

    item_id: str
    owner: str
    default: str
    group: str = ""
    label: str = ""


def _canonical(value: str) -> str:
    """统一成 ``F1`` / ``Alt+F5`` / ``Alt+M``；空串表示不绑定。"""
    text = str(value or "").replace(" ", "").strip()
    if not text:
        return ""
    parts = [part for part in text.split("+") if part]
    key = parts[-1].upper()
    modifiers = [part.lower() for part in parts[:-1]]
    canonical_mods = []
    for raw in modifiers:
        if raw in ("alt",):
            canonical_mods.append("Alt")
        elif raw in ("ctrl", "control"):
            canonical_mods.append("Ctrl")
        elif raw == "shift":
            canonical_mods.append("Shift")
        else:
            raise ValueError(f"不支持的修饰键: {raw}")
    if len(canonical_mods) > 1:
        raise ValueError("菜单快捷键只允许一个修饰键")
    return "+".join([*canonical_mods, key])


def zone_of(shortcut: str) -> str | None:
    """该键位属于哪个区间；字母键不分区间（返回 ``None``）。"""
    text = _canonical(shortcut)
    if not text:
        return None
    key = text.split("+")[-1]
    match = _F_KEY.match(key)
    if match is None:
        return None
    return _F_ZONES.get(int(match.group(1)))


def normalize_menu_shortcut(value: str, *, owner: str) -> str:
    """规范化菜单快捷键并校验区间；非法写法抛 ``ValueError``。

    ``owner`` 为 :data:`ZONE_CORE_MENU` 或 :data:`ZONE_PLUGIN_MENU`。
    """
    text = _canonical(value)
    if not text:
        return ""
    if text.lower() in RESERVED_COMBOS:
        raise ValueError(f"系统保留组合: {text}")
    key = text.split("+")[-1]
    zone = zone_of(text)
    if zone is None and not _LETTER.match(key):
        raise ValueError(f"不支持的菜单快捷键主键: {key}")
    if zone is not None and zone != owner:
        raise ValueError(f"键位 {text} 属于 {zone}，不能在 {owner} 使用")
    return text


def validate_menu_shortcuts(
    items: dict[str, str],
    *,
    owner: str,
) -> list[str]:
    """校验同一来源的整张表，返回可直接展示的问题列表（空表示通过）。"""
    problems: list[str] = []
    seen: dict[str, str] = {}
    for item_id, value in items.items():
        try:
            text = normalize_menu_shortcut(value, owner=owner)
        except ValueError as exc:
            problems.append(f"{item_id}: {exc}")
            continue
        if not text:
            continue
        if text in seen:
            problems.append(f"{item_id}: 与 {seen[text]} 绑定到同一个键位 {text}")
            continue
        seen[text] = item_id
    return problems


def cross_source_conflicts(shortcuts: dict[str, str]) -> list[str]:
    """检查跨来源重复：同一个键位被两个不同菜单项占用。

    ``shortcuts`` 的 key 需要带来源前缀（如 ``core:user_mgmt``、``plugin:game_config``），
    这样冲突信息能指出是哪两项撞了。
    """
    seen: dict[str, str] = {}
    problems: list[str] = []
    for name, value in shortcuts.items():
        text = str(value or "").strip()
        if not text:
            continue
        if text in seen:
            problems.append(f"{name} 与 {seen[text]} 绑定到同一个键位 {text}")
            continue
        seen[text] = name
    return problems
