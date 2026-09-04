"""词条数值占等级上限比例的唯一现算入口。

装备数据里的 ``cap_pct`` 是给调律 DSL 快速读取用的**派生缓存字段**，
它只在写盘时由本模块生成。Python 侧任何判定与计算都必须现算，不得回读
缓存——历史数据里 ``value`` 被改过而 ``cap_pct`` 没跟着重算的情况真实
存在（例如 value 已是 94% 承音上限、``cap_pct`` 却仍停在旧的 90.8），
拿它当权威会让超上限校验漏报、让换词条收益算错。
"""

from __future__ import annotations


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def affix_cap_ratio(
    level, affix_name: str, value, *, game_config=None,
) -> float | None:
    """现算 ``value`` 占该等级该词条上限的比例（1.0 表示满值）。

    上限数据缺失、等级/数值不可用时返回 None——调用方据此走「判不出来」
    的分支，而不是退回可能已经过期的 ``cap_pct``。
    """
    numeric = _number(value)
    if numeric is None or not isinstance(level, int) or level <= 0:
        return None
    if not affix_name:
        return None
    if game_config is None:
        from ..config import get_game_config
        game_config = get_game_config()
    caps = game_config.get_affix_caps(level, affix_name)
    cap = _number(caps.get("cap")) if caps else None
    if not cap:
        return None
    return numeric / cap


def affix_cap_pct(
    level, affix_name: str, value, *, game_config=None,
) -> float | None:
    """现算比例并按落库口径取 1 位小数百分比；无上限数据返回 None。"""
    ratio = affix_cap_ratio(
        level, affix_name, value, game_config=game_config)
    return None if ratio is None else round(ratio * 100, 1)


def affix_dict_cap_pct(affix, level, *, game_config=None) -> float | None:
    """dict 形态词条的现算百分比，供 UI/统计直接使用。"""
    if not isinstance(affix, dict):
        return None
    return affix_cap_pct(
        level, str(affix.get("name") or ""), affix.get("value"),
        game_config=game_config)


def equip_affix_cap_pcts(equip, *, game_config=None) -> list[float]:
    """现算装备 affix_1~5 的百分比列表，跳过无名或无上限数据的词条。"""
    if not isinstance(equip, dict):
        return []
    level = equip.get("level")
    if isinstance(level, str):
        try:
            level = int(level)
        except ValueError:
            return []
    result: list[float] = []
    for index in range(1, 6):
        pct = affix_dict_cap_pct(
            equip.get(f"affix_{index}"), level, game_config=game_config)
        if pct is not None:
            result.append(pct)
    return result


__all__ = [
    "affix_cap_ratio",
    "affix_cap_pct",
    "affix_dict_cap_pct",
    "equip_affix_cap_pcts",
]
