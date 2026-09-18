"""调律部位常量 —— 桌面 tuning_tab 与设备端 tuning_config 共用

刻意不依赖 PyQt：设备端（Chaquopy）也要 import，UI 框架相关的东西
一律不进来。分组结构与桌面「部位」页的两列 QGroupBox 一一对应。
"""
from __future__ import annotations

from ....i18n import tr
from .equipment_slots import SLOT_BY_KEY

#: 调律遍历分组：(组名, ((槽位 key, 显示名), ...))，顺序即 UI 展示顺序。
#: 这是调律扫描页的分组口径（“武器类”把首饰一起归入），不是游戏部位；
#: 槽位与显示名来自 equipment_slots 的唯一定义。
SLOT_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (tr("武器类"), tuple(
        (key, SLOT_BY_KEY[key].label)
        for key in ("main_weapon", "sub_weapon", "ring", "pendant"))),
    (tr("防具类"), tuple(
        (key, SLOT_BY_KEY[key].label)
        for key in ("head", "chest", "leg", "wrist"))),
)

#: 强制禁用的部位：主武器槽已展示全部武器，副武器无需遍历
LOCKED_SLOTS: frozenset[str] = frozenset({"sub_weapon"})

#: 部位 key → 中文标签（平铺视图）
SLOT_LABELS: dict[str, str] = {
    key: label for _, slots in SLOT_GROUPS for key, label in slots
}

#: 默认勾选的部位（全部可用部位，即排除禁用项）
DEFAULT_SLOTS: tuple[str, ...] = tuple(
    key for key in SLOT_LABELS if key not in LOCKED_SLOTS
)
