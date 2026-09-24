"""装备调律词条的候选计算助手。

词条组合的合法性判定不在这里——统一由
:mod:`lvjiang.apps.yysls.core.equip_validator` 负责，避免同一套游戏规则
存在两份实现各自漂移。
"""
from __future__ import annotations


def normal_affix_candidates(equip: dict, game_config) -> list[str]:
    """返回符合装备部位、武器类型和当前等级的普通词条候选。

    等级取装备自身的 ``level``：调律发生在装备当前等级上，115 装备调不出
    115 已经退役的词条。装备没带等级时不按等级收窄——这里是给「还能不能新
    产出」用的，不负责给缺等级的历史数据判罪；旧装备上已经存着的退役词条
    依然合法，那由 ``equip_validator`` 按词条注册表判。
    """
    equip_type = str(equip.get("type") or "")
    group = game_config.get_type_to_group().get(equip_type, "")
    part = game_config.get_group_to_part().get(group, "")
    weapon_affix = game_config.get_weapon_wuxue_affix(equip_type)
    weapon_affixes = set(game_config.get_wuxue_affix_names())
    level = _level_of(equip)

    return [
        name for name in game_config.get_normal_affix_names()
        if (name not in weapon_affixes or name == weapon_affix)
        and (not part or part in game_config.get_affix_parts(name))
        and game_config.is_affix_available(name, level)
    ]


def _level_of(equip: dict) -> int | None:
    try:
        level = int(equip.get("level") or 0)
    except (TypeError, ValueError):
        return None
    return level or None
