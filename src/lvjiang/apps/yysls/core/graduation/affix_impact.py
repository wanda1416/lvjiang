"""当前配装的词条敏感度与合法培养建议。

新增词条按当前赛季装备等级的普通词条满值计算；扣除词条按
当前装备的实际数值计算。此模块只分析 affix_1~5，定音词条不属于
可追加的普通词条，不纳入本报告。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

from .....i18n import tr
from ..combat.affix_rules import normal_affix_candidates
from ..combat.combat_attrs import (
    CombatAttributes,
    aggregate_equipment_attrs,
    build_graduation_attrs,
    compute_equip_base_attrs,
    map_affix_to_attr,
)
from ..equip_validator import validate_combination_dict

_DAMAGE_FIVE_DIMS = {"劲", "势", "敏"}
_JOINT_TARGETS_PER_AFFIX = 3
_JOINT_BEAM_WIDTH = 2048
_JOINT_INVALID_BEAM = 512


@dataclass(frozen=True)
class AffixImpact:
    """单个词条对毕业率的边际影响。"""

    name: str
    affix_value: float
    graduation_delta: float
    occurrence_count: int = 1


@dataclass(frozen=True)
class AffixImpactReport:
    """当前配装的词条收益报告。"""

    baseline_rate: float
    affix_level: int
    additions: tuple[AffixImpact, ...]
    removals: tuple[AffixImpact, ...]
    suggestions: tuple[AffixReplacementSuggestion, ...] = ()
    blocked_equipment: tuple[AffixBlockedEquipment, ...] = ()


@dataclass(frozen=True)
class AffixBlockedEquipment:
    """因当前组合异常而不能参与培养计算的装备。"""

    slot_key: str
    equipment_name: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class AffixReplacementSuggestion:
    """一条可通过转律实现的等品质词条替换建议。"""

    slot_key: str
    equipment_name: str
    affix_index: int
    from_name: str
    from_value: float
    to_name: str
    to_value: float
    cap_pct: float
    graduation_delta: float


@dataclass(frozen=True)
class AffixCombinationResult:
    """多个装备部位联合培养后的最优结果。"""

    selected_slots: tuple[str, ...]
    graduation_rate: float
    graduation_delta: float
    replacements: tuple[AffixReplacementSuggestion, ...]
    evaluated_combinations: int


def _number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _part_name(equip: dict, game_config) -> str:
    group = game_config.get_type_to_group().get(str(equip.get("type", "")), "")
    return game_config.get_group_to_part().get(group, "")


def _weapon_affix_for(equip: dict, game_config) -> str:
    return game_config.get_weapon_wuxue_affix(str(equip.get("type", "")))


def _iter_affixes(equipped: dict):
    for slot_key, equip in equipped.items():
        if not isinstance(equip, dict):
            continue
        for index in range(1, 6):
            field = f"affix_{index}"
            affix = equip.get(field)
            if not isinstance(affix, dict):
                continue
            name = str(affix.get("name") or "")
            value = _number(affix.get("value"))
            if name and value:
                yield slot_key, equip, field, name, value


def _effective_equipped(equipped: dict, game_config) -> dict:
    """归一化专属武学增效：只在匹配武器上生效，同类武器取最高一条。"""
    result = copy.deepcopy(equipped)
    weapon_affixes = set(game_config.get_all_weapon_wuxue_affixes().values())
    grouped: dict[tuple[str, str], list[tuple[float, str, str]]] = {}

    for slot_key, equip, field, name, value in _iter_affixes(result):
        if name not in weapon_affixes:
            continue
        expected = _weapon_affix_for(equip, game_config)
        if name != expected:
            result[slot_key].pop(field, None)
            continue
        weapon_type = str(equip.get("type", ""))
        grouped.setdefault((weapon_type, name), []).append(
            (value, slot_key, field))

    for occurrences in grouped.values():
        # 数值最高的一条生效；其余同类词条不参与毕业率计算。
        for _value, slot_key, field in sorted(occurrences, reverse=True)[1:]:
            result[slot_key].pop(field, None)
    return result


def _current_affix_level(game_config) -> int:
    season = game_config.current_season()
    if season is not None and season.equip_level:
        return int(season.equip_level)
    levels = game_config.get_level_configs()
    return max((int(item.level) for item in levels), default=0)


def _candidate_names(game_config) -> list[str]:
    """只保留能映射到伤害计算输入的普通词条，保持配置声明顺序。"""
    result: list[str] = []
    for names in game_config.get_affix_categories().values():
        for name in names:
            field_name, _is_percent = map_affix_to_attr(name)
            if name not in _DAMAGE_FIVE_DIMS and field_name is None:
                continue
            if name not in result:
                result.append(name)
    return result


def _can_add_affix(
    name: str,
    equipped: dict,
    game_config,
    effective_names: set[str],
) -> bool:
    weapon_map = game_config.get_all_weapon_wuxue_affixes()
    weapon_affixes = set(weapon_map.values())
    if name in weapon_affixes:
        # 专属武学增效只能加到绑定的武器类型，且同类只生效一条。
        if name in effective_names:
            return False
        return any(
            isinstance(equip, dict)
            and weapon_map.get(str(equip.get("type", ""))) == name
            for equip in equipped.values()
        )

    allowed_parts = set(game_config.get_affix_parts(name))
    return any(
        isinstance(equip, dict)
        and _part_name(equip, game_config) in allowed_parts
        for equip in equipped.values()
    )


def _graduation_rate(
    calculator,
    base_attrs: CombatAttributes,
    equipped: dict,
    school: str,
    game_config,
) -> float:
    equipment_attrs = compute_equip_base_attrs(
        equipped, game_config.get_base_attr_values,
    ) + aggregate_equipment_attrs(equipped)
    attrs = build_graduation_attrs(base_attrs, equipment_attrs, school)
    return float(calculator.calculate(attrs).graduation_rate)


def _affix_cap(game_config, level: int, name: str) -> float:
    caps = game_config.get_affix_caps(level, name)
    return _number(caps.get("cap")) if caps else 0.0


def _source_cap_pct(affix: dict, level: int, game_config) -> float:
    """优先采用扫描记录的满值百分比，否则由当前值和等级上限反推。"""
    recorded = _number(affix.get("cap_pct"))
    if recorded > 0:
        return min(recorded / 100, 1.0)
    cap = _affix_cap(game_config, level, str(affix.get("name") or ""))
    if cap <= 0:
        return 0.0
    return min(max(_number(affix.get("value")) / cap, 0.0), 1.0)


def _blocked_equipment(
    equipped: dict,
    *,
    selected_slots: set[str] | None = None,
) -> list[AffixBlockedEquipment]:
    blocked: list[AffixBlockedEquipment] = []
    for slot_key, equip in equipped.items():
        if selected_slots is not None and slot_key not in selected_slots:
            continue
        if not isinstance(equip, dict):
            continue
        flaws = validate_combination_dict(equip)
        if flaws:
            blocked.append(AffixBlockedEquipment(
                slot_key=slot_key,
                equipment_name=str(
                    equip.get("name") or equip.get("type") or slot_key),
                reasons=tuple(reason.message for reason in flaws),
            ))
    return blocked


def _replacement_candidates(
    equipped: dict,
    calculator,
    base_attrs: CombatAttributes,
    school: str,
    game_config,
    baseline_rate: float,
    fallback_level: int,
    *,
    selected_slots: set[str] | None = None,
    require_legal_result: bool,
) -> tuple[list[AffixReplacementSuggestion], list[AffixBlockedEquipment]]:
    """生成等品质替换候选，并隔离当前组合异常的装备。

    ``require_legal_result=False`` 仅用于联合搜索：单步替换可暂时重复，后续
    替换可能把冲突位置一并换走；最终方案仍必须通过完整合法性校验。
    """
    candidates_out: list[AffixReplacementSuggestion] = []
    blocked = _blocked_equipment(equipped, selected_slots=selected_slots)
    blocked_slots = {item.slot_key for item in blocked}
    for slot_key, equip in equipped.items():
        if selected_slots is not None and slot_key not in selected_slots:
            continue
        if (not isinstance(equip, dict) or equip.get("is_chengyin")
                or slot_key in blocked_slots):
            continue
        level = int(equip.get("level") or fallback_level)
        candidates = normal_affix_candidates(equip, game_config)
        for index in range(2, 6):
            field = f"affix_{index}"
            source = equip.get(field)
            if not isinstance(source, dict):
                continue
            from_name = str(source.get("name") or "")
            from_value = _number(source.get("value"))
            cap_pct = _source_cap_pct(source, level, game_config)
            if not from_name or from_value <= 0 or cap_pct <= 0:
                continue

            for to_name in candidates:
                # 转律不会产出神力词条；神力只能通过调律获得。
                if game_config.get_affix_category(to_name) in (
                    "增效类", "武器类",
                ):
                    continue
                if to_name == from_name:
                    continue
                to_value = _affix_cap(game_config, level, to_name) * cap_pct
                if to_value <= 0:
                    continue
                changed = copy.deepcopy(equipped)
                changed[slot_key][field] = {
                    "name": to_name,
                    "value": to_value,
                    "is_transferred": True,
                }
                if (require_legal_result
                        and validate_combination_dict(changed[slot_key])):
                    continue
                rate = _graduation_rate(
                    calculator,
                    base_attrs,
                    _effective_equipped(changed, game_config),
                    school,
                    game_config,
                )
                delta = rate - baseline_rate
                candidates_out.append(AffixReplacementSuggestion(
                    slot_key=slot_key,
                    equipment_name=str(equip.get("name") or equip.get("type") or slot_key),
                    affix_index=index,
                    from_name=from_name,
                    from_value=from_value,
                    to_name=to_name,
                    to_value=to_value,
                    cap_pct=cap_pct * 100,
                    graduation_delta=delta,
                ))
    return candidates_out, blocked


def _replacement_suggestions(
    equipped: dict,
    calculator,
    base_attrs: CombatAttributes,
    school: str,
    game_config,
    baseline_rate: float,
    fallback_level: int,
) -> tuple[list[AffixReplacementSuggestion], list[AffixBlockedEquipment]]:
    """为每个可转律词条找出毕业率提升最大的严格合法替换。"""
    candidates, blocked = _replacement_candidates(
        equipped,
        calculator,
        base_attrs,
        school,
        game_config,
        baseline_rate,
        fallback_level,
        require_legal_result=True,
    )
    best_by_position: dict[tuple[str, int], AffixReplacementSuggestion] = {}
    for candidate in candidates:
        key = (candidate.slot_key, candidate.affix_index)
        current = best_by_position.get(key)
        if current is None or candidate.graduation_delta > current.graduation_delta:
            best_by_position[key] = candidate
    suggestions = [
        item for item in best_by_position.values()
        if item.graduation_delta > 1e-9
    ]
    suggestions.sort(key=lambda item: (-item.graduation_delta, item.slot_key, item.affix_index))
    return suggestions, blocked


def _apply_replacements(
    equipped: dict,
    replacements: tuple[AffixReplacementSuggestion, ...],
) -> dict:
    changed = copy.deepcopy(equipped)
    for item in replacements:
        changed[item.slot_key][f"affix_{item.affix_index}"] = {
            "name": item.to_name,
            "value": item.to_value,
            "is_transferred": True,
        }
    return changed


def analyze_combined_affix_replacements(
    equipped: dict,
    _suggestions: tuple[AffixReplacementSuggestion, ...],
    selected_slots: tuple[str, ...],
    calculator,
    base_attrs: CombatAttributes,
    school: str,
    *,
    game_config=None,
) -> AffixCombinationResult:
    """搜索最多三个部位的多目标联合培养方案。

    每个词条位置保留多个高收益目标（而非只沿用表格里的单项第一名），再将
    「保持不变」与这些目标联合搜索。搜索规模超过上限时使用宽束搜索，最终
    结果必须通过整件装备的严格合法性校验，并重新计算整套毕业率。

    ``_suggestions`` 为兼容现有调用保留；联合候选会根据装备与配置重新生成。
    """
    if game_config is None:
        from ...config import get_game_config
        game_config = get_game_config()

    slots = tuple(dict.fromkeys(selected_slots))
    if not 1 <= len(slots) <= 3:
        raise ValueError(tr("联合培养需要选择 1-3 件装备"))
    all_blocked = _blocked_equipment(equipped)
    if all_blocked:
        names = "、".join(item.equipment_name for item in all_blocked)
        raise ValueError(tr("当前配装存在词条组合异常，请先校正：{names}").format(
            names=names,
        ))
    effective = _effective_equipped(equipped, game_config)
    baseline_rate = _graduation_rate(
        calculator, base_attrs, effective, school, game_config,
    )
    fallback_level = _current_affix_level(game_config)
    candidates, blocked = _replacement_candidates(
        equipped,
        calculator,
        base_attrs,
        school,
        game_config,
        baseline_rate,
        fallback_level,
        selected_slots=set(slots),
        require_legal_result=False,
    )
    if blocked:
        names = "、".join(item.equipment_name for item in blocked)
        raise ValueError(tr("所选装备存在词条组合异常，请先校正：{names}").format(
            names=names,
        ))

    by_position: dict[
        tuple[str, int], list[AffixReplacementSuggestion]
    ] = {}
    for candidate in candidates:
        by_position.setdefault(
            (candidate.slot_key, candidate.affix_index), [],
        ).append(candidate)
    for items in by_position.values():
        items.sort(key=lambda item: (-item.graduation_delta, item.to_name))
        del items[_JOINT_TARGETS_PER_AFFIX:]

    states: list[tuple[AffixReplacementSuggestion, ...]] = [()]
    for position in sorted(by_position):
        expanded = [
            state + ((candidate,) if candidate is not None else ())
            for state in states
            for candidate in (None, *by_position[position])
        ]
        if len(expanded) > _JOINT_BEAM_WIDTH:
            ranked: list[
                tuple[
                    int,
                    float,
                    int,
                    tuple[AffixReplacementSuggestion, ...],
                ]
            ] = []
            for state in expanded:
                changed = _apply_replacements(equipped, state)
                flaw_count = sum(
                    len(validate_combination_dict(changed[slot]))
                    for slot in {item.slot_key for item in state}
                )
                # 单项 delta 只作为束搜索排序启发，不当作最终收益；最终仍对
                # 整套属性重新计算。保留一部分暂时非法状态，允许后续位置
                # 换走冲突词条（例如 A→会心、原会心→精准）。
                heuristic = sum(item.graduation_delta for item in state)
                ranked.append((flaw_count, heuristic, len(state), state))
            ranked.sort(
                key=lambda row: (
                    -row[1], row[2],
                    tuple((item.slot_key, item.affix_index, item.to_name)
                          for item in row[3]),
                ),
            )
            legal = [row for row in ranked if row[0] == 0]
            temporarily_invalid = [row for row in ranked if row[0] > 0]
            invalid_limit = min(_JOINT_INVALID_BEAM, len(temporarily_invalid))
            legal_limit = _JOINT_BEAM_WIDTH - invalid_limit
            chosen = legal[:legal_limit] + temporarily_invalid[:invalid_limit]
            remaining = _JOINT_BEAM_WIDTH - len(chosen)
            if remaining:
                chosen += temporarily_invalid[
                    invalid_limit:invalid_limit + remaining
                ]
            states = [row[3] for row in chosen]
        else:
            states = expanded

    best_rate = baseline_rate
    best_replacements: tuple[AffixReplacementSuggestion, ...] = ()
    evaluated = 0
    for state in states:
        if not state:
            continue
        changed = _apply_replacements(equipped, state)
        affected_slots = {item.slot_key for item in state}
        if any(validate_combination_dict(changed[slot])
               for slot in affected_slots):
            continue
        evaluated += 1
        rate = _graduation_rate(
            calculator,
            base_attrs,
            _effective_equipped(changed, game_config),
            school,
            game_config,
        )
        if rate > best_rate + 1e-12 or (
            abs(rate - best_rate) <= 1e-12
            and best_replacements
            and len(state) < len(best_replacements)
        ):
            best_rate = rate
            best_replacements = state

    return AffixCombinationResult(
        selected_slots=slots,
        graduation_rate=best_rate,
        graduation_delta=best_rate - baseline_rate,
        replacements=best_replacements,
        evaluated_combinations=evaluated,
    )


def analyze_affix_impacts(
    equipped: dict,
    calculator,
    base_attrs: CombatAttributes,
    school: str,
    *,
    game_config=None,
    affix_level: int | None = None,
) -> AffixImpactReport:
    """计算当前配装可行的新增收益与实际词条扣除损失。"""
    if game_config is None:
        from ...config import get_game_config
        game_config = get_game_config()

    blocked_equipment = _blocked_equipment(equipped)

    effective = _effective_equipped(equipped, game_config)
    baseline_rate = _graduation_rate(
        calculator, base_attrs, effective, school, game_config,
    )
    level = int(affix_level or _current_affix_level(game_config))
    effective_names = {
        name for _slot, _equip, _field, name, _value in _iter_affixes(effective)
    }
    # 循环不变量：与候选词条无关，提到循环外算一次即可
    current_equipment_attrs = compute_equip_base_attrs(
        effective, game_config.get_base_attr_values,
    ) + aggregate_equipment_attrs(effective)

    additions: list[AffixImpact] = []
    for name in _candidate_names(game_config):
        if not _can_add_affix(name, effective, game_config, effective_names):
            continue
        caps = game_config.get_affix_caps(level, name)
        if not caps:
            continue
        value = _number(caps.get("cap"))
        if not value:
            continue
        delta_attrs = aggregate_equipment_attrs({
            "hypothetical": {"affix_1": {"name": name, "value": value}},
        })
        new_attrs = build_graduation_attrs(
            base_attrs, current_equipment_attrs + delta_attrs, school,
        )
        rate = float(calculator.calculate(new_attrs).graduation_rate)
        additions.append(AffixImpact(name, value, rate - baseline_rate))

    removal_candidates: dict[str, list[AffixImpact]] = {}
    occurrences: dict[str, int] = {}
    weapon_affixes = set(game_config.get_all_weapon_wuxue_affixes().values())
    for slot_key, _equip, field, name, value in _iter_affixes(effective):
        mapped, _is_percent = map_affix_to_attr(name)
        if name not in _DAMAGE_FIVE_DIMS and mapped is None:
            continue
        occurrences[name] = occurrences.get(name, 0) + 1
        changed = copy.deepcopy(
            equipped if name in weapon_affixes else effective)
        changed[slot_key].pop(field, None)
        # 扣除当前生效条目后，同类武器的次高条目可以接替生效。
        if name in weapon_affixes:
            changed = _effective_equipped(changed, game_config)
        rate = _graduation_rate(
            calculator, base_attrs, changed, school, game_config,
        )
        removal_candidates.setdefault(name, []).append(
            AffixImpact(name, value, rate - baseline_rate)
        )

    removals: list[AffixImpact] = []
    for name, impacts in removal_candidates.items():
        # 同名词条只展示“扣除其中损失最大的一条”，避免重复刷屏。
        impact = min(impacts, key=lambda item: item.graduation_delta)
        removals.append(AffixImpact(
            impact.name,
            impact.affix_value,
            impact.graduation_delta,
            occurrence_count=occurrences[name],
        ))

    additions.sort(key=lambda item: (-item.graduation_delta, item.name))
    removals.sort(key=lambda item: (item.graduation_delta, item.name))
    suggestions: list[AffixReplacementSuggestion] = []
    if not blocked_equipment:
        suggestions, _unused_blocked = _replacement_suggestions(
            equipped,
            calculator,
            base_attrs,
            school,
            game_config,
            baseline_rate,
            level,
        )
    return AffixImpactReport(
        baseline_rate=baseline_rate,
        affix_level=level,
        additions=tuple(additions),
        removals=tuple(removals),
        suggestions=tuple(suggestions),
        blocked_equipment=tuple(blocked_equipment),
    )
