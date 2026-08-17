"""调律部位常量 —— 桌面 tuning_tab 与设备端 tuning_config 共用

刻意不依赖 PyQt：设备端（Chaquopy）也要 import，UI 框架相关的东西
一律不进来。分组结构与桌面「部位」页的两列 QGroupBox 一一对应。
"""
from __future__ import annotations

from ....i18n import tr

#: 部位分组：(组名, ((部位 key, 中文标签), ...))，顺序即 UI 展示顺序
SLOT_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (tr("武器类"), (("main_weapon", tr("主武器")), ("sub_weapon", tr("副武器")),
               ("ring", tr("环")), ("pendant", tr("佩")))),
    (tr("防具类"), (("head", tr("冠胄")), ("chest", tr("胸甲")),
               ("leg", tr("胫甲")), ("wrist", tr("腕甲")))),
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
