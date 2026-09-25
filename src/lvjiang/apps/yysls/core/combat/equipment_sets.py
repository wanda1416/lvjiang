"""装备套装的统一属性计算。

左四套装的两件套属性属于整套组合，不能摊进单件装备。评分、基础属性反推
和最优组合都必须调用这里，避免同一套装在不同入口得到不同结果。
"""

from __future__ import annotations

from collections import Counter

from .combat_attrs import CombatAttributes, map_affix_to_attr

LEFT_SET_SLOTS = ("main_weapon", "sub_weapon", "ring", "pendant")
RIGHT_SET_SLOTS = ("head", "chest", "leg", "wrist")


def _tier_value(average_level: float, affix_name: str, game_config) -> float | None:
    """按所在相邻等级档取整档值或上下档中点，不做线性插值。"""
    levels = game_config.get_affix_cap_levels(affix_name)
    for level in levels:
        if average_level == level:
            caps = game_config.get_affix_caps(level, affix_name)
            return float(caps["cap"]) if caps else None
    lower = max((level for level in levels if level < average_level), default=None)
    upper = min((level for level in levels if level > average_level), default=None)
    if lower is None or upper is None:
        return None
    lower_caps = game_config.get_affix_caps(lower, affix_name)
    upper_caps = game_config.get_affix_caps(upper, affix_name)
    if not lower_caps or not upper_caps:
        return None
    return (float(lower_caps["cap"]) + float(upper_caps["cap"])) / 2


def equipment_set_bonus(equipped: dict[str, dict], game_config) -> CombatAttributes:
    """计算完整左四装备的两件套属性；信息不完整时保守返回零。

    历史数据没有套装信息时，扫描面板反推出来的基础属性已经包含旧套装加成。
    因而四件左侧装备必须全部有合法套装和等级，才允许显式累计。
    """
    registry = game_config.get_equipment_sets("left")
    raw_left = [equipped.get(slot) for slot in LEFT_SET_SLOTS]
    if any(not isinstance(equip, dict) for equip in raw_left):
        return CombatAttributes()
    left: list[dict] = [equip for equip in raw_left if isinstance(equip, dict)]
    set_keys = [str(equip.get("equipment_set") or "") for equip in left]
    levels = [float(equip.get("level") or 0) for equip in left]
    if any(key not in registry for key in set_keys) or any(level <= 0 for level in levels):
        return CombatAttributes()

    average_level = sum(levels) / len(levels)
    result = CombatAttributes()
    for set_key, count in Counter(set_keys).items():
        if count < 2:
            continue
        affix_name = str(registry[set_key].get("two_piece_affix") or "")
        field_name, is_percent = map_affix_to_attr(affix_name)
        value = _tier_value(average_level, affix_name, game_config)
        if field_name is None or value is None:
            continue
        normalized = value / 100 if is_percent else value
        if hasattr(result, field_name):
            setattr(result, field_name, getattr(result, field_name) + normalized)
        else:
            result.extra_attrs[field_name] = (
                result.extra_attrs.get(field_name, 0.0) + normalized)
    return result
