"""承音/再次转律产生的同一实体装备历史快照识别。"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from ...config.models import LevelConfig
from ..equip_parser.dingyin_parser import is_zhige_dingyin

_EPSILON = 1e-6
_KNOWN_QUALITIES = frozenset({"gold", "purple", "blue", "green"})


@dataclass(frozen=True)
class ChengyinMergeCandidate:
    """一组疑似同件装备；old 应删除，new 应保留。"""

    old_fp: str
    new_fp: str
    old: dict
    new: dict


def _affixes(equip: dict) -> list[dict] | None:
    affix_keys = {
        key for key in equip
        if isinstance(key, str) and key.startswith("affix_")
        and key.removeprefix("affix_").isdigit()
    }
    if affix_keys != {f"affix_{index}" for index in range(1, 6)}:
        return None
    result: list[dict] = []
    for index in range(1, 6):
        value = equip.get(f"affix_{index}")
        if not isinstance(value, dict) or not value.get("name"):
            return None
        result.append(value)
    return result


def _has_dingyin(equip: dict) -> bool:
    dingyin = equip.get("dingyin")
    return (
        isinstance(dingyin, dict) and bool(dingyin.get("name"))
    ) or is_zhige_dingyin(equip)


def _eligible(equip: dict, levels: dict[int, LevelConfig]) -> bool:
    if not isinstance(equip, dict):
        return False
    if bool((equip.get("_extra") or {}).get("is_mock")):
        return False
    level = equip.get("level")
    config = levels.get(level) if isinstance(level, int) else None
    return bool(
        config
        and config.allow_chengyin
        and equip.get("type")
        and equip.get("quality") in _KNOWN_QUALITIES
        and _affixes(equip) is not None
        and _has_dingyin(equip)
    )


def _transferred_index(affixes: list[dict]) -> tuple[bool, int | None]:
    indices = [
        index for index, affix in enumerate(affixes)
        if bool(affix.get("is_transferred"))
    ]
    # 游戏只有一个固定转律槽；异常多标数据不参与自动候选。
    if len(indices) > 1:
        return False, None
    return True, indices[0] if indices else None


def _numeric_value(affix: dict) -> float | None:
    value = affix.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _can_follow(
    old: dict,
    new: dict,
    levels: dict[int, LevelConfig],
) -> bool:
    """new 是否可能是 old 经承音、传律或转律后的版本。"""
    if not _eligible(old, levels) or not _eligible(new, levels):
        return False
    if old.get("type") != new.get("type"):
        return False
    if old.get("quality") != new.get("quality"):
        return False

    old_level = old.get("level")
    new_level = new.get("level")
    if not isinstance(old_level, int) or not isinstance(new_level, int):
        return False
    if new_level < old_level:
        return False
    # 承音标记一旦出现不能倒退；未承音装备之间仍可因首次/再次转律匹配。
    if bool(old.get("is_chengyin")) and not bool(new.get("is_chengyin")):
        return False

    old_affixes = _affixes(old)
    new_affixes = _affixes(new)
    assert old_affixes is not None and new_affixes is not None
    old_valid, old_transferred = _transferred_index(old_affixes)
    new_valid, new_transferred = _transferred_index(new_affixes)
    if not old_valid or not new_valid:
        return False
    if old_transferred == 0 or new_transferred == 0:
        return False

    # 已转律不能恢复为未转律，也不能移动到另一个词条位置。
    if old_transferred is not None and new_transferred != old_transferred:
        return False

    changed = (
        old_level != new_level
        or bool(old.get("is_chengyin")) != bool(new.get("is_chengyin"))
        or old_transferred != new_transferred
    )
    for index, (old_affix, new_affix) in enumerate(
        zip(old_affixes, new_affixes, strict=True)
    ):
        old_name = str(old_affix.get("name") or "")
        new_name = str(new_affix.get("name") or "")
        names_differ = old_name != new_name
        if names_differ:
            # 第1词条不可转律；名称变化只能发生在新版本的固定转律槽。
            if index == 0 or new_transferred != index:
                return False
            if old_transferred is not None:
                # 双方都已转律时属于再次转律：两边等级都必须支持无限转律。
                old_cfg = levels[old_level]
                new_cfg = levels[new_level]
                if not (old_cfg.allow_retransfer and new_cfg.allow_retransfer):
                    return False
            changed = True
            # 不同词条的数值/上限没有可比性。
            continue

        old_value = _numeric_value(old_affix)
        new_value = _numeric_value(new_affix)
        if old_value is None or new_value is None:
            return False
        if new_value + _EPSILON < old_value:
            return False
        if new_value > old_value + _EPSILON:
            changed = True

    return changed


def find_chengyin_merge_candidates(
    equipment_items: dict[str, dict],
    level_configs: list[LevelConfig],
) -> list[ChengyinMergeCandidate]:
    """返回所有疑似同件装备对；字典中较晚插入者用于打破双向平局。"""
    levels = {config.level: config for config in level_configs}
    eligible = [
        (position, str(fp), equip)
        for position, (fp, equip) in enumerate(equipment_items.items())
        if _eligible(equip, levels)
    ]
    result: list[ChengyinMergeCandidate] = []
    for left, right in combinations(eligible, 2):
        left_pos, left_fp, left_equip = left
        right_pos, right_fp, right_equip = right
        left_to_right = _can_follow(left_equip, right_equip, levels)
        right_to_left = _can_follow(right_equip, left_equip, levels)
        if not left_to_right and not right_to_left:
            continue
        if left_to_right and not right_to_left:
            old_fp, new_fp = left_fp, right_fp
            old, new = left_equip, right_equip
        elif right_to_left and not left_to_right:
            old_fp, new_fp = right_fp, left_fp
            old, new = right_equip, left_equip
        elif left_pos < right_pos:
            old_fp, new_fp = left_fp, right_fp
            old, new = left_equip, right_equip
        else:
            old_fp, new_fp = right_fp, left_fp
            old, new = right_equip, left_equip
        result.append(ChengyinMergeCandidate(
            old_fp=old_fp,
            new_fp=new_fp,
            old=old,
            new=new,
        ))
    return result


__all__ = ["ChengyinMergeCandidate", "find_chengyin_merge_candidates"]
