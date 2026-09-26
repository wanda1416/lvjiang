"""内建来源：装备固有值与五维 → 战斗属性的转换

转换系数不进 YAML。DIY 计算器的角色基础五维口径与装备词条口径只有
``劲 → 最小外功`` 不同：角色基础为 0.22，装备词条为 0.225；其余系数
复用 :mod:`combat_attrs` 的游戏规则常量。

因此 YAML 只负责声明角色**有多少**劲/势/敏（来自等级底子、突破、
心法等来源写入 ``dim_*`` 字段），转换本身在第二趟求值里作为公式
完成——五维必须先加完，公式才读得到最终值。

体/御 只产出生命值与外功防御，不在 CombatAttributes 里，故不转换，
与 ``convert_five_dims`` 的取舍一致。
"""

from __future__ import annotations

from ..combat.combat_attrs import (
    JIN_TO_MAX_OUTER,
    MIN_TO_CRIT_RATE,
    MIN_TO_MIN_OUTER,
    SHI_TO_INTENT_RATE,
    SHI_TO_MAX_OUTER,
    CombatAttributes,
    compute_equip_base_attrs,
    compute_gongjue_attrs,
)
from .models import Formula, StatEffect

#: 内建条目的 id 前缀，便于在 breakdown 里与 YAML 来源区分
BUILTIN_PREFIX = "内建·"

# DIY 计算器 0.10.1：角色满养成五维使用 1 劲 → 0.22 最小外功。
# 装备词条继续使用 combat_attrs.JIN_TO_MIN_OUTER = 0.225。
ROLE_JIN_TO_MIN_OUTER = 0.22

DIMENSION_JIN = f"{BUILTIN_PREFIX}五维·劲"
DIMENSION_SHI = f"{BUILTIN_PREFIX}五维·势"
DIMENSION_MIN = f"{BUILTIN_PREFIX}五维·敏"
EQUIPMENT_BASE = f"{BUILTIN_PREFIX}装备固有值"
GONGJUE = f"{BUILTIN_PREFIX}弓玦"


def full_gold_equipment_attrs(level: int, base_attr_lookup) -> CombatAttributes:
    """返回当前等级八件金装的固有属性，不含 40 条词条、定音和弓玦。

    固有攻击的权威值仍来自 ``game_config/equipment.yaml``；这里仅构造
    八个槽位交给既有聚合函数，避免在属性模型里平行硬编码 380 / 757。
    当前只有双武器、环、佩提供已建模的外功攻击，四件防具仍保留在
    完整槽位快照中，后续补生存属性时无需改变这条契约。
    """
    equipped = {
        slot: {"level": int(level), "quality": "gold"}
        for slot in (
            "main_weapon", "sub_weapon", "head", "chest",
            "ring", "pendant", "leg", "wrist",
        )
    }
    return compute_equip_base_attrs(equipped, base_attr_lookup)


def equipment_base_effect(level: int, base_attr_lookup) -> StatEffect:
    """把满级八件金装固有值包装成可拆分的内建来源。"""
    attrs = full_gold_equipment_attrs(level, base_attr_lookup)
    return StatEffect(
        source_id=EQUIPMENT_BASE,
        label=f"{level}阶八件金装固有值",
        kind="equipment_base",
        stats={
            "min_outer": attrs.min_outer,
            "max_outer": attrs.max_outer,
        },
    )


def gongjue_effect(
    gongjue_type: str, level: int, affix_caps_lookup,
) -> StatEffect | None:
    """按独立等级生成弓玦来源；115 装备可以继续搭配 110 弓玦。"""
    if not gongjue_type or not level:
        return None
    attrs = compute_gongjue_attrs(gongjue_type, level, affix_caps_lookup)
    stats: dict[str, float | Formula] = {
        name: float(getattr(attrs, name))
        for name in ("precision", "crit_rate", "intent_rate")
        if getattr(attrs, name)
    }
    return StatEffect(
        source_id=f"{GONGJUE}·{level}·{gongjue_type}",
        label=f"{level}阶{gongjue_type}弓玦",
        kind="gongjue",
        stats=stats,
    )


def dimension_effects() -> list[StatEffect]:
    """五维转换效果，一维一条，便于 breakdown 定位到具体维度"""
    return [
        StatEffect(
            source_id=DIMENSION_JIN,
            label="五维·劲",
            kind="dimension",
            stats={
                "min_outer": Formula(source="dim_jin", multiplier=ROLE_JIN_TO_MIN_OUTER),
                "max_outer": Formula(source="dim_jin", multiplier=JIN_TO_MAX_OUTER),
            },
        ),
        StatEffect(
            source_id=DIMENSION_SHI,
            label="五维·势",
            kind="dimension",
            stats={
                "max_outer": Formula(source="dim_shi", multiplier=SHI_TO_MAX_OUTER),
                "intent_rate": Formula(source="dim_shi", multiplier=SHI_TO_INTENT_RATE),
            },
        ),
        StatEffect(
            source_id=DIMENSION_MIN,
            label="五维·敏",
            kind="dimension",
            stats={
                "min_outer": Formula(source="dim_min", multiplier=MIN_TO_MIN_OUTER),
                "crit_rate": Formula(source="dim_min", multiplier=MIN_TO_CRIT_RATE),
            },
        ),
    ]
