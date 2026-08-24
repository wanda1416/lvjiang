"""装备调律词条的候选计算助手。

词条组合的合法性判定不在这里——统一由
:mod:`lvjiang.apps.yysls.core.equip_validator` 负责，避免同一套游戏规则
存在两份实现各自漂移。
"""
from __future__ import annotations


def normal_affix_candidates(equip: dict, game_config) -> list[str]:
    """返回符合装备部位和武器类型的普通词条候选。"""
    equip_type = str(equip.get("type") or "")
    group = game_config.get_type_to_group().get(equip_type, "")
    part = game_config.get_group_to_part().get(group, "")
    weapon_affix = game_config.get_weapon_wuxue_affix(equip_type)
    weapon_affixes = set(game_config.get_wuxue_affix_names())

    return [
        name for name in game_config.get_normal_affix_names()
        if (name not in weapon_affixes or name == weapon_affix)
        and (not part or part in game_config.get_affix_parts(name))
    ]
