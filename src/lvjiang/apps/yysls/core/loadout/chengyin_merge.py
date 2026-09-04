"""承音/再次转律产生的同一实体装备历史快照识别。"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from ...config.models import LevelConfig
from ..equip_parser.dingyin_parser import is_zhige_dingyin

_EPSILON = 1e-6
_KNOWN_QUALITIES = frozenset({"gold", "purple", "blue", "green"})
# 同一个真值可能以两种口径落库：游戏只显示 1 位小数（OCR 得到 114.1），
# 而内部承音上限是 round(cap * 0.94, 2)（手填得到 114.12）。两者的舍入
# 方向彼此独立，5 条词条里几乎必然有一条反向偏移，用浮点 epsilon 比较
# 会把同一件装备判成「数值下降」。可比的公共分辨率是显示步长，容差取
# 半步：喂养带来的真实提升至少 0.1 才在游戏里可见，不会被这个容差吃掉。
_DISPLAY_STEP = 0.1
_VALUE_TOL = _DISPLAY_STEP / 2 + _EPSILON
# 判断哪份快照更新时参考的可选字段；缺失说明该份记录更简略。
_DETAIL_FIELDS = ("original_level", "created_at", "cooldown_expires_at")


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


def _freshness(equip: dict, position: int) -> tuple:
    """双向兼容时判断哪份快照更「新」。

    cap_pct 是给 DSL 快查用的派生缓存，可能与 value 早已脱节，不作依据。
    改用可信的元数据：更新时间 > 记录完整度 > 插入顺序。
    """
    updated_at = equip.get("updated_at")
    detail = sum(
        1 for field in _DETAIL_FIELDS if equip.get(field)
    )
    return (str(updated_at or ""), detail, position)


def _can_follow(
    old: dict,
    new: dict,
    levels: dict[int, LevelConfig],
) -> bool:
    """new 是否可能是 old 经承音、传律或转律后的版本。

    这里判定的是「不矛盾」而非「确有演进」：同一件装备完全可能被记录成
    两份数值一致的快照——喂养中途重复录入，或一份手填满承音、一份实测，
    两者仅因小数位口径不同才生成了不同指纹。要求必须存在可见变化会把这
    类重复快照永远挡在候选之外，而它们正是最该合并的对象。
    """
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
            # 不同词条的数值/上限没有可比性。
            continue

        old_value = _numeric_value(old_affix)
        new_value = _numeric_value(new_affix)
        if old_value is None or new_value is None:
            return False
        if new_value < old_value - _VALUE_TOL:
            return False

    return True


def find_chengyin_merge_candidates(
    equipment_items: dict[str, dict],
    level_configs: list[LevelConfig],
) -> list[ChengyinMergeCandidate]:
    """返回所有疑似同件装备对。

    双向兼容（互为对方的合法后继）时由 :func:`_freshness` 决定保留哪份。
    """
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
        elif (_freshness(left_equip, left_pos)
              < _freshness(right_equip, right_pos)):
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
