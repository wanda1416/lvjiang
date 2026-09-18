"""模拟转律：资格判定、目标合法性与三满投影。

转律目标只是写在公共装备普通词条上的计划（``target_transmute_name`` /
``target_transmute_value``），不改变 ``name``/``value``、不参与指纹。本模块
只负责判断哪些装备、哪些槽可以转，目标是否仍然合法，以及在计算副本上把
目标覆盖进去；搜索由 ``core.graduation.transmute_optimizer`` 负责。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

from ..affix_cap import affix_cap_value
from ..combat.affix_rules import normal_affix_candidates
from ..equip_validator import validate_combination_dict

TARGET_NAME_KEY = "target_transmute_name"
TARGET_VALUE_KEY = "target_transmute_value"

#: 转律只能改商角徵羽；宫（第 1 条）永远不动。
TRANSMUTABLE_INDICES: tuple[int, ...] = (2, 3, 4, 5)

# 不合格原因代码；界面按代码翻译成文案。
REASON_UNKNOWN_ORIGINAL_LEVEL = "unknown_original_level"
REASON_NO_RETRANSFER = "no_retransfer"
REASON_NO_RETRANSFER_AFTER_CHENGYIN = "no_retransfer_after_chengyin"
REASON_ILLEGAL = "illegal"
REASON_MULTIPLE_TRANSFERRED = "multiple_transferred"
REASON_FIRST_TRANSFERRED = "first_transferred"
REASON_NO_SLOTS = "no_slots"
REASON_NO_LEVEL_CONFIG = "no_level_config"


@dataclass(frozen=True)
class TransmuteEligibility:
    """一件装备的转律资格与可转槽位。"""

    eligible: bool
    reason: str = ""
    slots: tuple[int, ...] = ()
    trusted: bool = True  # False：装备数据自相矛盾，整体结果不可信


def _number(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def present_affix_indices(equip: dict) -> list[int]:
    return [
        index for index in range(1, 6)
        if isinstance(equip.get(f"affix_{index}"), dict)
        and (equip.get(f"affix_{index}") or {}).get("name")
    ]


def transferred_indices(equip: dict) -> list[int]:
    return [
        index for index in present_affix_indices(equip)
        if bool((equip.get(f"affix_{index}") or {}).get("is_transferred"))
    ]


def retransfer_capability(equip: dict, game_config) -> tuple[bool, str]:
    """这件装备能否再次转律（改已转律的固定槽）：(允许, 不允许原因代码)。

    未承音看当前等级的 ``allow_retransfer``；已承音看名称识别出的原始等级，
    且要求 ``allow_retransfer_after_chengyin`` 同时为真——承音后的当前等级
    不代表装备来源。三处转律模拟（规则评级潜力、智能调律、备战方案转律
    建议）共用这一条规则。
    """
    is_chengyin = bool(equip.get("is_chengyin"))
    if is_chengyin:
        original = _int(equip.get("original_level"))
        if original <= 0:
            original = game_config.infer_original_equipment_level(
                equip.get("name"))
        if original <= 0:
            return False, REASON_UNKNOWN_ORIGINAL_LEVEL
        cfg = game_config.level_config_for(original)
        if cfg is None:
            return False, REASON_NO_LEVEL_CONFIG
        if not cfg.allow_retransfer:
            return False, REASON_NO_RETRANSFER
        if not cfg.allow_retransfer_after_chengyin:
            return False, REASON_NO_RETRANSFER_AFTER_CHENGYIN
        return True, ""
    cfg = game_config.level_config_for(_int(equip.get("level")))
    if cfg is None:
        return False, REASON_NO_LEVEL_CONFIG
    if not cfg.allow_retransfer:
        return False, REASON_NO_RETRANSFER
    return True, ""


def judge_transmute_eligibility(
    equip: dict, game_config, *, require_retransfer: bool = True,
) -> TransmuteEligibility:
    """判定能否参与模拟转律，以及允许改哪些槽（在原始装备快照上判断）。

    ``require_retransfer=True``（备战方案转律建议）：只支持一次转律的装备
    即使尚未转律也不参与——转错一次没有退路，程序不替用户做这个决定。
    ``False``（自动调律的评级潜力与智能调律）：尚未转律的装备总可以转一次；
    已转律的才看再次转律能力。
    """
    if not isinstance(equip, dict):
        return TransmuteEligibility(False, REASON_ILLEGAL)
    if validate_combination_dict(equip):
        return TransmuteEligibility(False, REASON_ILLEGAL, trusted=False)
    transferred = transferred_indices(equip)
    if 1 in transferred:
        return TransmuteEligibility(
            False, REASON_FIRST_TRANSFERRED, trusted=False)
    if len(transferred) > 1:
        return TransmuteEligibility(
            False, REASON_MULTIPLE_TRANSFERRED, trusted=False)

    if transferred or require_retransfer:
        allowed, reason = retransfer_capability(equip, game_config)
        if not allowed:
            return TransmuteEligibility(False, reason)
    elif game_config.level_config_for(_int(equip.get("level"))) is None:
        # 首次转律不看再次转律能力，但等级本身必须有配置——没有上限数据
        # 就没有目标值，判定层直接说清，不靠下游取不到上限兜住。
        return TransmuteEligibility(False, REASON_NO_LEVEL_CONFIG)

    if transferred:
        slots: tuple[int, ...] = (transferred[0],)
    else:
        slots = tuple(
            index for index in present_affix_indices(equip)
            if index in TRANSMUTABLE_INDICES)
    if not slots:
        return TransmuteEligibility(False, REASON_NO_SLOTS)
    return TransmuteEligibility(True, "", slots)


def transmute_pool_union(game_config) -> list[str]:
    """全部流派转律词条库的并集，保持配置声明顺序。

    游戏里转律可以任选任一流派的词条库，所以不用本流派池；任何库都没有的
    词条永远不是转律目标。未配置的流派返回空列表，不影响并集。
    """
    result: list[str] = []
    for names in game_config.get_all_transmute_pools().values():
        for name in names:
            if name not in result:
                result.append(name)
    return result


def transmute_target_value(
    name: str, level: int, is_chengyin: bool, game_config,
) -> float | None:
    """目标词条数值：未承音取普通上限（彩狗粮 100%），承音取承音上限。

    两者都是词组配置原值（见 ``affix_cap_value``），不按比例推算。
    """
    return affix_cap_value(
        int(level or 0), name, chengyin=is_chengyin, game_config=game_config)


def with_transmuted_affix(
    equip: dict, index: int, name: str, value: float, game_config,
) -> dict:
    """返回把第 ``index`` 条替换为转律产出 ``name``/``value`` 的装备副本。"""
    changed = copy.deepcopy(equip)
    caps = game_config.get_affix_caps(_int(equip.get("level")), name) or {}
    changed[f"affix_{index}"] = {
        "name": name,
        "value": value,
        "unit": caps.get("unit") or None,
        "is_transferred": True,
    }
    return changed


def transmute_targets(
    equip: dict,
    index: int,
    pool: list[str],
    game_config,
) -> list[str]:
    """第 ``index`` 条转出后的合法转入词条，保持 ``pool`` 顺序。

    过滤链：``pool``（调用方决定口径：备战方案用各流派转律库并集，智能调律
    用并集 ∩ 规则池）∩ 部位/武器物理可出现 − 第 2～5 条已有名字（首词条是
    装备自带的，产出允许与之同名）− 与原名相同；再逐个替换后过整件合法性
    校验。
    """
    source = equip.get(f"affix_{index}")
    if not isinstance(source, dict) or not source.get("name"):
        return []
    physical = set(normal_affix_candidates(equip, game_config))
    present = {
        str((equip.get(f"affix_{i}") or {}).get("name") or "")
        for i in present_affix_indices(equip)
        if i in TRANSMUTABLE_INDICES
    }
    level = _int(equip.get("level"))
    is_chengyin = bool(equip.get("is_chengyin"))
    legal: list[str] = []
    for name in pool:
        if name not in physical or name in present:
            continue
        value = transmute_target_value(name, level, is_chengyin, game_config)
        if value is None:
            continue
        if validate_combination_dict(
                with_transmuted_affix(equip, index, name, value, game_config)):
            continue
        legal.append(name)
    return legal


def transmute_candidates(
    equip: dict,
    game_config,
    pool_union: list[str] | None = None,
    *,
    slots: tuple[int, ...] | None = None,
) -> dict[int, list[str]]:
    """备战方案口径：按槽位列出合法目标词条（各流派转律库并集）。"""
    if pool_union is None:
        pool_union = transmute_pool_union(game_config)
    if not pool_union:
        return {}
    if slots is None:
        eligibility = judge_transmute_eligibility(equip, game_config)
        if not eligibility.eligible:
            return {}
        slots = eligibility.slots
    result: dict[int, list[str]] = {}
    for index in slots:
        legal = transmute_targets(equip, index, pool_union, game_config)
        if legal:
            result[index] = legal
    return result


def saved_transmute_target(equip: dict) -> tuple[int, str, float] | None:
    """读取装备上保存的目标；两字段必须同时有效，且一件装备只认第一个。"""
    if not isinstance(equip, dict):
        return None
    for index in range(1, 6):
        affix = equip.get(f"affix_{index}")
        if not isinstance(affix, dict):
            continue
        name = str(affix.get(TARGET_NAME_KEY) or "").strip()
        value = _number(affix.get(TARGET_VALUE_KEY))
        if name and value > 0:
            return index, name, value
    return None


def validate_saved_target(equip: dict, game_config) -> str | None:
    """已保存目标是否仍合法；返回不合法原因代码，合法返回 None。"""
    saved = saved_transmute_target(equip)
    if saved is None:
        return None
    index, name, _value = saved
    eligibility = judge_transmute_eligibility(equip, game_config)
    if not eligibility.eligible:
        return eligibility.reason or REASON_ILLEGAL
    if index not in eligibility.slots:
        return REASON_NO_SLOTS
    candidates = transmute_candidates(
        equip, game_config, slots=(index,))
    if name not in candidates.get(index, []):
        return REASON_ILLEGAL
    return None


def strip_transmute_targets(equip: dict) -> dict:
    """原地删除装备上的全部目标字段，返回同一对象。"""
    if not isinstance(equip, dict):
        return equip
    for index in range(1, 6):
        affix = equip.get(f"affix_{index}")
        if isinstance(affix, dict):
            affix.pop(TARGET_NAME_KEY, None)
            affix.pop(TARGET_VALUE_KEY, None)
    return equip


def project_transmute_targets(
    original: dict[str, dict],
    projected: dict[str, dict],
    game_config,
) -> dict[str, dict]:
    """在三满投影后的副本上覆盖已保存目标，返回新副本。

    资格与合法性在 ``original``（原始快照）上判断；数值按 ``projected``
    副本当时的等级/承音状态取——满等级把副本升级为承音时取承音上限，
    这样"105 未承音先转律再承音"的顺序在计算里成立。不合法的目标跳过。
    """
    result: dict[str, dict] = {}
    for slot_key, equip in projected.items():
        source = original.get(slot_key)
        if (not isinstance(equip, dict) or not isinstance(source, dict)
                or validate_saved_target(source, game_config) is not None):
            result[slot_key] = equip
            continue
        saved = saved_transmute_target(source)
        if saved is None:
            result[slot_key] = equip
            continue
        index, name, _stored = saved
        value = transmute_target_value(
            name, _int(equip.get("level")), bool(equip.get("is_chengyin")),
            game_config)
        if value is None:
            result[slot_key] = equip
            continue
        changed = with_transmuted_affix(equip, index, name, value, game_config)
        result[slot_key] = strip_transmute_targets(changed)
    return result
