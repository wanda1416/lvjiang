"""手机备战只读视图；计算和模型选择复用公共燕云能力。"""
from __future__ import annotations

import copy
import json
from dataclasses import asdict
from datetime import datetime


def _repository(username: str):
    from ....core.ondevice.task_runner import _local_user_names
    from ..core.loadout.repository import LoadoutRepository

    if username not in _local_user_names():
        raise ValueError("用户资料不可用")
    return LoadoutRepository(username)


def _json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False)


def _equipment(fingerprint: str, item: dict) -> dict:
    """展示字段由领域数据派生，不把内部结构交给 Android 解释。"""
    from ..config import get_game_config
    from ..core.equip_parser.models import EquipmentData
    from ..core.tuning_rules.models import QUALITY_LABELS

    equip = EquipmentData.from_dict(item)
    sections: list[dict] = []
    base_items = []
    for attr in (equip.base_attr, equip.base_attr_2):
        if attr:
            value = "～".join(map(str, attr.value)) if isinstance(attr.value, list) else str(attr.value)
            base_items.append({"label": attr.name, "value": value})
    if base_items:
        sections.append({"title": "基础属性", "items": base_items})
    affixes = []
    for affix in equip.affixes:
        note = "原" if affix.is_original else "转" if affix.is_transferred else ""
        affixes.append({"label": affix.name, "value": f"{affix.value:g}{affix.unit or ''}", "note": note})
    if affixes:
        sections.append({"title": "词条", "items": affixes})
    tuning = []
    for label, dingyin in (("定音", equip.dingyin), ("止戈定音", equip.dingyin_zhige)):
        if dingyin:
            tuning.append({"label": dingyin.get("name", label),
                           "value": f"{dingyin.get('value', '')}{dingyin.get('unit') or ''}", "note": label})
    if tuning:
        sections.append({"title": "定音", "items": tuning})
    metadata = [f"{equip.level}级" if equip.level else "等级未记录", equip.type or "部位未记录"]
    if equip.quality in QUALITY_LABELS:
        metadata.append(QUALITY_LABELS[equip.quality])
    if equip.is_chengyin:
        metadata.append("承音")
    if item.get("equipment_set"):
        metadata.append(get_game_config().equipment_set_name(str(item["equipment_set"])) or "套装未识别")
    if item.get("is_mock"):
        metadata.append("模拟装备")
    stamp = item.get("last_seen_at") or item.get("updated_at") or item.get("created_at") or ""
    recorded = ""
    if stamp:
        try:
            recorded = f"{datetime.fromisoformat(str(stamp)).astimezone():%Y-%m-%d %H:%M}"
        except ValueError:
            recorded = "时间未识别"
    return {"id": fingerprint, "name": equip.name or "未命名装备", "summary": " · ".join(metadata),
            "sections": sections, "recorded_at": recorded, "warnings": equip.warnings, "is_mock": bool(item.get("is_mock"))}


def list_plans(username: str) -> str:
    try:
        state = _repository(username).load()
        return _json({"ok": True, "selected": state.active_plan_id, "equipment_count": len(state.equipment_items),
                      "plans": [{"id": p.id, "name": p.name} for p in state.plans.values()]})
    except Exception as exc:
        return _json({"ok": False, "message": str(exc)})


def equipment_pool(username: str, offset: int = 0) -> str:
    try:
        state = _repository(username).load()
        items = sorted(state.equipment_items.items(),
                       key=lambda pair: str(pair[1].get("last_seen_at") or pair[1].get("updated_at") or ""), reverse=True)
        offset = max(0, offset)
        rows = []
        for fingerprint, item in items[offset:offset + 30]:
            try:
                rows.append(_equipment(fingerprint, item))
            except (ValueError, TypeError, KeyError) as exc:
                rows.append({"id": fingerprint, "name": item.get("name") or "装备记录", "summary": "记录无法解析",
                             "warnings": [f"请重新扫描此装备：{exc}"], "sections": []})
        return _json({"ok": True, "items": rows, "total": len(items), "offset": offset})
    except Exception as exc:
        return _json({"ok": False, "message": str(exc)})


def plan_view(username: str, plan_id: str, scheme: str = "", base_attribute: str = "", assumptions: str = "{}") -> str:
    from ..config import get_game_config
    from ..config.equipment_slots import SLOT_SPECS
    from ..config.play_styles import get_play_styles
    from ..core.combat.combat_attrs import (
        CombatAttributes,
        GraduationAttrContext,
        fold_wuxiang_pen,
        format_value,
    )
    from ..core.graduation.assumptions import Assumptions
    from ..core.graduation.context import (
        PlanContextError,
        PlanScoringContext,
        gongjue_attrs,
    )
    from ..core.graduation.model_registry import (
        available_models,
        select_graduation_model,
    )
    from ..core.graduation.scoring import equipment_attrs, graduation_input
    from ..core.loadout.models import plan_school

    try:
        state = _repository(username).load()
        if plan_id not in state.plans:
            raise ValueError("备战方案已不存在，请刷新")
        plan = copy.deepcopy(state.plans[plan_id])
        gc = get_game_config()
        school = plan_school(plan, gc.get_schools())
        level = state.effective_world_level(gc.current_equip_level())
        models = available_models(school) if school else ()
        schemes = sorted({ref.scheme for ref in models if ref.level <= level})
        bases = get_play_styles(school) if school else {}
        if scheme:
            if scheme not in schemes:
                raise ValueError("计算方案不适用于当前流派和等级")
            plan.graduation_scheme = scheme
        if base_attribute:
            if base_attribute not in bases:
                raise ValueError("基础属性不适用于当前流派")
            plan.base_attribute = base_attribute
        equipped = state.resolved_equipment(plan_id)
        flags = json.loads(assumptions)
        keys = ("full_level", "full_chengyin", "full_dingyin", "simulate_transmute")
        if not isinstance(flags, dict) or set(flags) - set(keys) or any(type(v) is not bool for v in flags.values()):
            raise ValueError("计算假设参数无效")
        hypothesis = Assumptions(full_level=level if flags.get("full_level") else 0,
                                 full_chengyin=flags.get("full_chengyin", False),
                                 full_dingyin=flags.get("full_dingyin", False),
                                 simulate_transmute=flags.get("simulate_transmute", False), playstyle=plan.playstyle)
        single_options = {
            "full_level": Assumptions(full_level=level),
            "full_chengyin": Assumptions(full_chengyin=True),
            "full_dingyin": Assumptions(full_dingyin=True),
            "simulate_transmute": Assumptions(simulate_transmute=True),
        }
        options = [{"key": key, "label": single_options[key].labels()[0],
                    "checked": flags.get(key, False)} for key in keys]
        projected = hypothesis.project(equipped, gc)
        slots = []
        problems = []
        for spec in SLOT_SPECS:
            item = equipped.get(spec.key)
            row: dict = {"key": spec.key, "label": spec.label, "equipment": None}
            if item:
                try:
                    row["equipment"] = _equipment(str(plan.equipment[spec.key]), item)
                except (ValueError, TypeError, KeyError):
                    problems.append(f"{spec.label}装备记录无法解析，请重新扫描")
            else:
                problems.append(f"{spec.label}未记录装备")
            slots.append(row)
        result = None
        combat = []
        if school and plan.base_attribute in bases:
            attr_context = GraduationAttrContext.from_school(school, world_level=level, game_config=gc)
            base = CombatAttributes.from_dict(bases[plan.base_attribute]) + gongjue_attrs(
                plan.gongjue, gc, world_level=level, gongjue_level=plan.gongjue_level)
            raw = base + fold_wuxiang_pen(equipment_attrs(projected, gc), attr_context.target_pen_field)
            effective = graduation_input(base, projected, school, gc, attr_context=attr_context)
            effective_items = {label: value for label, value, _ in effective.display_items()}
            combat = [{"label": label, "raw": format_value(value, unit),
                       "effective": format_value(effective_items.get(label, value), unit)}
                      for label, value, unit in raw.display_items()]
        model = select_graduation_model(school, plan.graduation_scheme, level) if school else None
        try:
            context = PlanScoringContext.from_plan(plan, game_config=gc, world_level=level)
            if not problems:
                scorer = context.scorer(game_config=gc)
                result = asdict(context.calculator.calculate(scorer.attrs(projected)))
        except PlanContextError as exc:
            problems.append(exc.reason)
        except Exception as exc:
            problems.append(f"毕业率计算失败：{exc}")
        return _json({"ok": True, "plan_name": plan.name, "school": school, "world_level": level,
                      "scheme": plan.graduation_scheme, "schemes": schemes, "base_attribute": plan.base_attribute,
                      "base_attributes": sorted(bases), "model": asdict(model) if model else None,
                      "result": result, "reasons": problems, "slots": slots,
                      "assumptions": options, "assumption_labels": hypothesis.labels(), "combat_attributes": combat,
                      "combat_cards": _combat_cards(combat),
                      "contains_mock": any(bool(item.get("is_mock")) for item in equipped.values())})
    except Exception as exc:
        return _json({"ok": False, "message": str(exc)})


def _combat_cards(items: list[dict]) -> list[dict]:
    """按桌面四张属性卡的展示职责分组，值仍取公共属性快照。"""
    from ..core.combat.combat_attrs import COMBAT_ATTR_FIELDS, THREE_RATE_FIELDS

    keys = {label: key for key, label, _, _ in COMBAT_ATTR_FIELDS}
    groups: dict[str, list[dict]] = {name: [] for name in ("攻击属性", "判定属性", "增益效果", "伤害加成")}
    for item in items:
        key = keys.get(item["label"], "")
        if key.startswith(("min_", "max_")):
            group = "攻击属性"
        elif key in THREE_RATE_FIELDS or key.startswith("direct_"):
            group = "判定属性"
        elif key.endswith("_dmg") or key in {"outer_bonus", "mingjin_bonus", "lieshi_bonus", "pozhu_bonus", "qiansi_bonus"}:
            group = "伤害加成"
        else:
            group = "增益效果"
        groups[group].append(item)
    return [{"title": name, "items": values} for name, values in groups.items()] if items else []
