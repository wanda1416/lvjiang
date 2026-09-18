"""装备槽位（8 个）的唯一定义 —— 备战方案页、最优组合、分析对话框、调律部位共用。

术语必须分清：

- **槽位（slot）**：备战方案上的 8 个位置，key 为 ``main_weapon`` / ``sub_weapon`` /
  ``head`` / ``chest`` / ``ring`` / ``pendant`` / ``leg`` / ``wrist``。
- **部位（part）**：游戏里的 7 个装备部位——武器、冠胄、胸甲、环、佩、胫甲、腕甲。
  主武器和副武器是**两个槽位、同一个部位（武器）**：词条池、词条上限、部位合法性
  都按“武器”看，只有背包分发和穿戴位置按槽位区分。

刻意不依赖 PyQt 与 core：设备端也要 import。
"""
from __future__ import annotations

from dataclasses import dataclass

from ....i18n import tr

PART_WEAPON = "武器"


@dataclass(frozen=True)
class SlotSpec:
    key: str          # 槽位 key
    label: str        # 显示名
    part: str         # 游戏部位（主/副武器都是“武器”）
    filter_type: str  # 背包筛选类型（主/副武器都是 weapon）
    row: int          # 4×2 网格行
    col: int          # 4×2 网格列

    @property
    def is_weapon(self) -> bool:
        return self.part == PART_WEAPON


#: 8 个槽位，顺序即 4×2 网格顺序（第一行武器 + 上身防具，第二行首饰 + 下身防具）
SLOT_SPECS: tuple[SlotSpec, ...] = (
    SlotSpec("main_weapon", tr("主武器"), PART_WEAPON, "weapon", 0, 0),
    SlotSpec("sub_weapon", tr("副武器"), PART_WEAPON, "weapon", 0, 1),
    SlotSpec("head", tr("冠胄"), "冠胄", "head", 0, 2),
    SlotSpec("chest", tr("胸甲"), "胸甲", "chest", 0, 3),
    SlotSpec("ring", tr("环"), "环", "ring", 1, 0),
    SlotSpec("pendant", tr("佩"), "佩", "pendant", 1, 1),
    SlotSpec("leg", tr("胫甲"), "胫甲", "leg", 1, 2),
    SlotSpec("wrist", tr("腕甲"), "腕甲", "wrist", 1, 3),
)

SLOT_BY_KEY: dict[str, SlotSpec] = {spec.key: spec for spec in SLOT_SPECS}

#: 槽位 key 元组（网格顺序）
EQUIPMENT_SLOTS: tuple[str, ...] = tuple(spec.key for spec in SLOT_SPECS)

#: 槽位 key → 显示名
SLOT_LABELS: dict[str, str] = {spec.key: spec.label for spec in SLOT_SPECS}

#: 武器槽位（主 + 副）；两者同属“武器”部位
WEAPON_SLOTS: tuple[str, ...] = tuple(
    spec.key for spec in SLOT_SPECS if spec.is_weapon)


def slot_part(slot_key: str) -> str:
    """槽位 → 游戏部位；未知槽位返回空串。"""
    spec = SLOT_BY_KEY.get(slot_key)
    return spec.part if spec else ""


def grid_layout() -> tuple[tuple[int, int, str], ...]:
    """(行, 列, 槽位 key) 序列，供 4×2 卡片网格使用。"""
    return tuple((spec.row, spec.col, spec.key) for spec in SLOT_SPECS)
