"""模拟装备词条交换：按交换后的双方状态判断，复用游戏产出规则。"""
from __future__ import annotations

import copy

from ..combat.affix_rules import normal_affix_candidates
from ..equip_validator import validate_combination_dict


def swap_affixes(equipment: dict[str, dict], source: tuple[str, int],
                 target: tuple[str, int], game_config) -> tuple[dict[str, dict] | None, str]:
    if source == target:
        return None, "当前位置"
    if any(slot not in equipment or index not in range(1, 6)
           for slot, index in (source, target)):
        return None, "无效词条位置"
    changed = copy.deepcopy(equipment)
    a, ai = source
    b, bi = target
    old_a = changed[a].get(f"affix_{ai}")
    old_b = changed[b].get(f"affix_{bi}")
    if not old_a:
        return None, "源位置没有词条"
    if (ai == 1 and not old_b) or (bi == 1 and not old_a):
        return None, "首词条不能为空"
    changed[a][f"affix_{ai}"], changed[b][f"affix_{bi}"] = old_b, old_a
    for slot in dict.fromkeys((a, b)):
        equip = changed[slot]
        group = game_config.get_type_to_group().get(equip.get("type"), "")
        first = (equip.get("affix_1") or {}).get("name")
        if first not in game_config.get_first_affixes(
                group, equip.get("original_level") or equip.get("level")):
            return None, "首词条不允许出现在该部位或等级"
        if a != b:
            allowed = set(normal_affix_candidates(equip, game_config))
            incoming_index = ai if slot == a else bi
            incoming = equip.get(f"affix_{incoming_index}")
            if incoming_index > 1 and incoming and incoming["name"] not in allowed:
                return None, "词条不允许出现在该部位、武器或等级"
        reasons = validate_combination_dict(equip)
        if reasons:
            return None, "；".join(reason.message for reason in reasons)
    return changed, ""
