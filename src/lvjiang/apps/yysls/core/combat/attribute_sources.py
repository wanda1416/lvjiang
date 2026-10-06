"""当前战斗属性的只读来源报告，复用公共装备与抗性计算。"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from ...config.equipment_slots import SLOT_LABELS
from ..graduation.scoring import equipment_attrs
from .combat_attrs import (
    FIVE_DIM_NAMES,
    CombatAttributes,
    GraduationAttrContext,
    aggregate_equipment_attrs,
    build_graduation_attrs,
    compute_equip_base_attrs,
    effective_equipped,
    fold_wuxiang_pen,
)
from .equipment_sets import LEFT_SET_SLOTS, equipment_set_bonus

SOURCE_CATEGORIES = ("base", "gongjue", "equipment_set", "affix", "dingyin", "equipment_base")


@dataclass(frozen=True)
class AttributeContribution:
    category: str
    label: str
    attrs: CombatAttributes


@dataclass(frozen=True)
class AttributeSourceReport:
    contributions: tuple[AttributeContribution, ...]
    raw: CombatAttributes
    effective: CombatAttributes
    context: GraduationAttrContext
    excluded: tuple[str, ...]

    def category_attrs(self, category: str) -> CombatAttributes:
        total = CombatAttributes()
        for contribution in self.contributions:
            if contribution.category == category:
                total = total + contribution.attrs
        return total


def build_attribute_source_report(
    base: CombatAttributes, gongjue: CombatAttributes, equipped: dict,
    *, context: GraduationAttrContext, school: str, game_config,
) -> AttributeSourceReport:
    """装备已由调用方完成假设投影；按整套归一化结果拆分有效项。"""
    effective = effective_equipped(equipped, game_config)
    contributions = [
        AttributeContribution("base", "", deepcopy(base)),
        AttributeContribution("gongjue", "", deepcopy(gongjue)),
    ]
    excluded: list[str] = []

    def add(category: str, label: str, attrs: CombatAttributes) -> None:
        contributions.append(AttributeContribution(
            category, label, fold_wuxiang_pen(attrs, context.target_pen_field)))

    left = [item if isinstance(item, dict) else {}
            for slot in LEFT_SET_SLOTS for item in (effective.get(slot),)]
    registry = game_config.get_equipment_sets("left")
    set_names = list(dict.fromkeys(
        registry.get(item.get("equipment_set"), {}).get("name", "") for item in left))
    set_label = "、".join(name for name in set_names if name)
    if set_label:
        average = sum(float(item.get("level") or 0) for item in left) / len(left)
        set_label += f"（攻具平均 {average:.1f} 级）"
    add("equipment_set", set_label, equipment_set_bonus(effective, game_config))
    for slot, item in effective.items():
        if not isinstance(item, dict):
            continue
        slot_label = SLOT_LABELS.get(slot, slot)
        add("equipment_base", slot_label, compute_equip_base_attrs(
            {slot: item}, game_config.get_base_attr_values))
        original = equipped.get(slot) or {}
        for key in (*(f"affix_{index}" for index in range(1, 6)), "dingyin"):
            affix = item.get(key)
            if not isinstance(affix, dict):
                omitted = original.get(key)
                if isinstance(omitted, dict) and omitted.get("name") and omitted.get("value"):
                    excluded.append(f"{slot_label} · {omitted['name']}")
                continue
            name = str(affix.get("name") or "")
            if not name or not affix.get("value"):
                continue
            # 已全局处理部位与同名互斥规则，单项聚合不能再次归一化。
            isolated = {key: affix}
            attrs = aggregate_equipment_attrs({slot: isolated}, normalize=False)
            label = f"{slot_label} · {name}"
            if name in FIVE_DIM_NAMES:
                label += f" {float(affix['value']):.1f}（五维转换）"
            add("dingyin" if key == "dingyin" else "affix", label, attrs)

    # 总值仍以评分内核为准，来源明细只是对它的解释。
    folded = fold_wuxiang_pen(equipment_attrs(equipped, game_config), context.target_pen_field)
    return AttributeSourceReport(
        tuple(contributions), base + gongjue + folded,
        build_graduation_attrs(base + gongjue, folded, school, context=context),
        context, tuple(excluded),
    )
