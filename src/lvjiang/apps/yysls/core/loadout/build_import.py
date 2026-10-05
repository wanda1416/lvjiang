"""出装搭配生成模拟装备，并原子应用到指定备战方案。"""
from __future__ import annotations

import copy

from ...config.builds import BuildDefinition
from ...config.equipment_slots import EQUIPMENT_SLOTS, SLOT_LABELS, SLOT_SPECS
from ..affix_cap import affix_cap_pct, affix_cap_value
from ..combat.affix_rules import normal_affix_candidates
from ..combat.combat_attrs import apply_hypothetical_caps
from ..equip_parser.models import make_fingerprint
from ..equip_validator import validate_combination_dict
from .models import LoadoutPlan
from .repository import LoadoutRepository, stamp_equipment_write


def materialize_build(build: BuildDefinition, game_config) -> dict[str, dict]:
    """按保存的槽位生成满值金装；不重新分配，不修改搭配。"""
    build.validate()
    if set(build.equipment) != set(EQUIPMENT_SLOTS):
        raise ValueError("搭配必须包含八件装备")
    style = game_config.get_playstyle(build.playstyle)
    if not style:
        raise ValueError("搭配的玩法已不存在")
    gongjue_level = build.gongjue_level or game_config.gongjue_level_for(build.level)
    if build.gongjue and game_config.get_gongjue_bonus(build.gongjue, gongjue_level) is None:
        raise ValueError("搭配的弓玦套装或等级没有有效配置")
    base_config = game_config.get_raw().get("base_attrs", {})
    result: dict[str, dict] = {}
    for spec in SLOT_SPECS:
        template = build.equipment[spec.key]
        if not isinstance(template, dict):
            raise ValueError(f"{spec.label}装备数据格式异常")
        for i in range(1, 6):
            affix = template.get(f"affix_{i}")
            if affix is not None and (not isinstance(affix, dict) or not isinstance(affix.get("name"), str)):
                raise ValueError(f"{spec.label}第 {i} 条词条数据格式异常")
        expected_type = (style.get("main_weapon" if spec.key == "main_weapon" else "sub_weapon")
                         if spec.is_weapon else spec.part)
        kind = template.get("type")
        if not expected_type or kind != expected_type:
            raise ValueError(f"{spec.label}装备类型与搭配玩法不匹配")
        group = game_config.get_type_to_group().get(kind)
        first = (template.get("affix_1") or {}).get("name")
        if first not in game_config.get_first_affixes(group, build.level):
            raise ValueError(f"{spec.label}缺少该等级合法首词条")
        allowed = set(normal_affix_candidates({"type": kind, "level": build.level}, game_config))
        equip: dict = {
            "type": kind, "name": build.name, "level": build.level,
            "original_level": build.level, "quality": "gold", "is_chengyin": build.chengyin,
            "equipment_set": str(template.get("equipment_set") or ""),
            "base_attr_2": None,
        }
        side = "left" if group in ("weapon", "ring", "pendant") else "right"
        if equip["equipment_set"] and equip["equipment_set"] not in game_config.get_equipment_sets(side):
            raise ValueError(f"{spec.label}套装已不存在或不适用于该部位")
        low, high = game_config.get_base_attr_values(group, build.level, "gold")
        base = base_config.get(group, {})
        base = base_config.get(base.get("_follow"), {}) if base.get("_follow") else base
        if low is None or high is None or not base.get("_attr"):
            raise ValueError(f"{spec.label}缺少该等级金装基础属性")
        equip["base_attr"] = {"name": base["_attr"], "value": [low, high] if spec.is_weapon else low}
        for i in range(1, 6):
            name = (template.get(f"affix_{i}") or {}).get("name")
            if not name:
                continue
            if i > 1 and name not in allowed:
                raise ValueError(f"{spec.label}第 {i} 条词条不适用于该部位、武器或等级")
            cap = affix_cap_value(build.level, name, chengyin=build.chengyin, game_config=game_config)
            if cap is None:
                raise ValueError(f"{spec.label}词条「{name}」缺少数值上限")
            caps = game_config.get_affix_caps(build.level, name) or {}
            equip[f"affix_{i}"] = {"name": name, "value": cap, "unit": caps.get("unit") or None}
        reasons = validate_combination_dict(equip)
        if reasons:
            raise ValueError(f"{spec.label}：" + "；".join(r.message for r in reasons))
        dingyin = game_config.get_playstyle_dingyin(build.playstyle, kind)
        if dingyin and not game_config.get_affix_caps(build.level, dingyin):
            raise ValueError(f"{spec.label}缺少该等级定音数值上限")
        result[spec.key] = equip
    result = apply_hypothetical_caps(result, full_dingyin=True, playstyle=build.playstyle)
    for equip in result.values():
        for key in [*(f"affix_{i}" for i in range(1, 6)), "dingyin"]:
            affix = equip.get(key)
            if affix:
                pct = affix_cap_pct(build.level, affix["name"], affix["value"], game_config=game_config)
                if pct is not None:
                    affix["cap_pct"] = pct
        equip["_extra"] = {"is_mock": True, "affix_count": sum(bool(equip.get(f"affix_{i}")) for i in range(1, 6))}
        equip["_fp"] = make_fingerprint(equip, is_mock=True)
    return result


def equipment_for_plan(build: BuildDefinition, plan: LoadoutPlan, game_config) -> dict[str, dict]:
    if plan.playstyle != build.playstyle:
        raise ValueError("搭配玩法与目标备战方案不匹配")
    arts = (game_config.get_playstyle(build.playstyle) or {}).get("arts", [])
    if len(arts) != 2 or set(arts) != {plan.main_martial_art, plan.sub_martial_art}:
        raise ValueError("目标方案的主副武学与搭配玩法不匹配")
    result = materialize_build(build, game_config)
    if plan.main_martial_art != arts[0]:
        result["main_weapon"], result["sub_weapon"] = result["sub_weapon"], result["main_weapon"]
    return result


def _same_mock(existing: dict, incoming: dict) -> bool:
    # 名称、时间和套装不决定装备实体；套装应用到方案覆盖，不能修改共享装备。
    fields = ("type", "level", "quality", "is_chengyin", "base_attr", "base_attr_2",
              *(f"affix_{i}" for i in range(1, 6)), "dingyin")
    if existing.get("equipment_set") and not incoming.get("equipment_set"):
        # 空套装覆盖在现有模型中表示沿用装备自身，不能让“无套装”继承旧套装。
        return False

    def content(equip, key):
        value = equip.get(key) or None
        if isinstance(value, dict):
            return {
                **{k: value.get(k) for k in (
                    "name", "value", "target_transmute_name", "target_transmute_value")},
                "unit": value.get("unit") or None,
                "is_transferred": bool(value.get("is_transferred")),
                "is_original": bool(value.get("is_original")),
            }
        return value

    return bool((existing.get("_extra") or {}).get("is_mock")) and all(
        content(existing, key) == content(incoming, key) for key in fields)


def import_build(repo: LoadoutRepository, plan_id: str, build: BuildDefinition,
                 *, expected_plan: dict, game_config):
    """生成、校验并一次性落盘；不覆盖已有装备，不改变其他方案或活动选择。"""
    build = copy.deepcopy(build)

    def mutate(state):
        plan = state.plans.get(plan_id)
        if plan is None or plan.to_dict() != expected_plan:
            raise ValueError("目标备战方案已修改或删除，请重新打开导入窗口")
        if build.level > state.effective_world_level(game_config.current_equip_level()):
            raise ValueError("搭配等级高于当前用户世界等级，请选择适用等级的搭配")
        equipment = equipment_for_plan(build, plan, game_config)
        for slot, equip in equipment.items():
            fp = equip["_fp"]
            existing = state.equipment_items.get(fp)
            if existing is not None and not _same_mock(existing, equip):
                raise ValueError(f"{SLOT_LABELS[slot]}存在同指纹但内容不同的模拟装备，导入已取消；请检查现有装备")
            if existing is None:
                state.equipment_items[fp] = stamp_equipment_write(equip, fp, None)
            plan.clear_slot(slot)
            plan.equipment[slot] = fp
            if equip["equipment_set"]:
                plan.equipment_sets[slot] = equip["equipment_set"]
        plan.gongjue = build.gongjue
        plan.gongjue_level = build.gongjue_level or game_config.gongjue_level_for(build.level)
        plan.combat_type = build.combat_type

    return repo.update(mutate)
