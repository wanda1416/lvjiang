"""伤害建模：技能系数表与增益表

毕业率方案 JSON 里的 ``program`` 是从 Excel 编译出来的表达式图，能精确
复现整张表，但读不出「第一道剑气的外功倍率是 1.3066」。本包把这部分
显式建模成可读、可改的配置，作为那份程序的参考层。

求值仍走编译程序——两份公式就是两份真相。
"""

from .manager import (
    DamageModelManager,
    get_damage_model_manager,
    invalidate_damage_model_cache,
)
from .models import (
    FORCE_FLAGS,
    MODIFIER_FIELDS,
    RATIO_FIELDS,
    DamageBuff,
    DamageModel,
    DamageModelError,
    DamageSkill,
)
from .parsing import parse_model, parse_skill

__all__ = [
    "FORCE_FLAGS",
    "MODIFIER_FIELDS",
    "RATIO_FIELDS",
    "DamageBuff",
    "DamageModel",
    "DamageModelError",
    "DamageModelManager",
    "DamageSkill",
    "get_damage_model_manager",
    "invalidate_damage_model_cache",
    "parse_model",
    "parse_skill",
]
