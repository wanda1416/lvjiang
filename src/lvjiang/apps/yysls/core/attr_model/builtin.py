"""内建来源：五维 → 战斗属性的转换

转换系数不进 YAML。装备词条上的五维已经由
:func:`combat_attrs.convert_five_dims` 转换，系数写在
``combat_attrs`` 里；本模块直接引用同一组常数，避免两处各存一份
导致改一处漏一处。

因此 YAML 只负责声明角色**有多少**劲/势/敏（来自等级底子、突破、
心法等来源写入 ``dim_*`` 字段），转换本身在第二趟求值里作为公式
完成——五维必须先加完，公式才读得到最终值。

体/御 只产出生命值与外功防御，不在 CombatAttributes 里，故不转换，
与 ``convert_five_dims`` 的取舍一致。
"""

from __future__ import annotations

from ..combat.combat_attrs import (
    JIN_TO_MAX_OUTER,
    JIN_TO_MIN_OUTER,
    MIN_TO_CRIT_RATE,
    MIN_TO_MIN_OUTER,
    SHI_TO_INTENT_RATE,
    SHI_TO_MAX_OUTER,
)
from .models import Formula, StatEffect

#: 内建条目的 id 前缀，便于在 breakdown 里与 YAML 来源区分
BUILTIN_PREFIX = "内建·"

DIMENSION_JIN = f"{BUILTIN_PREFIX}五维·劲"
DIMENSION_SHI = f"{BUILTIN_PREFIX}五维·势"
DIMENSION_MIN = f"{BUILTIN_PREFIX}五维·敏"


def dimension_effects() -> list[StatEffect]:
    """五维转换效果，一维一条，便于 breakdown 定位到具体维度"""
    return [
        StatEffect(
            source_id=DIMENSION_JIN,
            label="五维·劲",
            kind="dimension",
            stats={
                "min_outer": Formula(source="dim_jin", multiplier=JIN_TO_MIN_OUTER),
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
