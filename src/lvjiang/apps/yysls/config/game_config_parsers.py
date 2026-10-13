"""配置字段解析工具与领域校验，不持有独立缓存。"""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal, InvalidOperation
from typing import TypeGuard

from ....i18n import tr
from .constants import (
    _CHENGYIN_RATIO,
)
from .models import (
    AffixUpgrade,
    TuningStoneRule,
)


def _parse_affix_upgrades(raw_rule: object) -> list[AffixUpgrade]:
    """把一条 ``_upgrades`` 配置展开成逐个旧词条的规则。

    ``from`` 允许写一个名字或一组名字：几个旧词条合并成同一个新词条是常态，
    拆成多行只会让同一条规则散开。
    """
    if not isinstance(raw_rule, dict):
        return []
    from_level = raw_rule.get("from_level")
    to_level = raw_rule.get("to_level")
    to_name = str(raw_rule.get("to") or "").strip()
    if not _positive_int(from_level) or not _positive_int(to_level):
        return []
    if not to_name or from_level >= to_level:
        return []
    raw_from = raw_rule.get("from")
    from_names = raw_from if isinstance(raw_from, list) else [raw_from]
    operation = str(raw_rule.get("operation") or "chengyin")
    rules = []
    for raw_name in from_names:
        from_name = str(raw_name or "").strip()
        if from_name:
            rules.append(AffixUpgrade(
                from_level=from_level, to_level=to_level,
                from_name=from_name, to_name=to_name, operation=operation))
    return rules


def _positive_int(value: object) -> TypeGuard[int]:
    return (isinstance(value, int) and not isinstance(value, bool)
            and value > 0)


def _parse_date(value) -> date | None:
    """解析日期值：支持 date 对象、ISO 格式字符串（YYYY-MM-DD），无效返回 None"""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


SEASON_SWITCH_HOUR = 5
_SEASON_SWITCH_TIME = time(SEASON_SWITCH_HOUR, 0)


def validate_season_configs(raw_seasons) -> None:
    """校验赛季时间线；相邻赛季必须在同一天 05:00 无缝切换。"""
    if raw_seasons is None:
        return
    if not isinstance(raw_seasons, list):
        raise ValueError(tr("赛季配置必须是列表"))

    seasons: list[tuple[int, date, date]] = []
    seen_numbers: set[int] = set()
    for index, item in enumerate(raw_seasons, 1):
        if not isinstance(item, dict):
            raise ValueError(tr("第 {row} 条赛季配置格式不正确").format(row=index))
        number = item.get("season_number")
        if not isinstance(number, int) or number <= 0:
            raise ValueError(tr("第 {row} 条赛季编号不合法").format(row=index))
        if number in seen_numbers:
            raise ValueError(tr("赛季编号不可重复：{number}").format(number=number))
        seen_numbers.add(number)

        start = _parse_date(item.get("start_date"))
        end = _parse_date(item.get("end_date"))
        if start is None or end is None:
            raise ValueError(tr("赛季 {number} 必须填写开始和结束日期").format(
                number=number,
            ))
        if start >= end:
            raise ValueError(tr("赛季 {number} 的结束日期必须晚于开始日期").format(
                number=number,
            ))
        seasons.append((number, start, end))

    seasons.sort(key=lambda item: item[0])
    for previous, following in zip(seasons, seasons[1:], strict=False):
        previous_number, _previous_start, previous_end = previous
        following_number, following_start, _following_end = following
        if previous_end != following_start:
            raise ValueError(tr(
                "相邻赛季日期必须衔接：赛季 {previous} 的结束日期必须等于"
                "赛季 {following} 的开始日期（05:00 切换）"
            ).format(
                previous=previous_number,
                following=following_number,
            ))


def _string_list(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(
        str(item).strip() for item in value if str(item).strip()))


def _stone_units(value, where: str) -> int:
    """配置里的大律准石等价数转为 0.1 枚的整数单位。"""
    if isinstance(value, bool):
        raise ValueError(f"{where} 必须是非负数（最多 1 位小数）")
    try:
        scaled = Decimal(str(value)) * 10
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{where} 必须是非负数（最多 1 位小数）") from exc
    if not scaled.is_finite() or scaled < 0 or scaled != scaled.to_integral_value():
        raise ValueError(f"{where} 必须是非负数（最多 1 位小数）")
    return int(scaled)


def _stone_map(raw, where: str) -> dict[int, int]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{where} 必须是字典")
    result: dict[int, int] = {}
    for key, value in raw.items():
        try:
            affix_count = int(key)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{where} 的词条数必须是 1-5") from exc
        if affix_count not in range(1, 6):
            raise ValueError(f"{where} 的词条数必须是 1-5")
        result[affix_count] = _stone_units(value, f"{where}.{affix_count}")
    return result


def _parse_tuning_stones(raw, where: str) -> dict[str, TuningStoneRule]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"{where} 必须是字典")
    result: dict[str, TuningStoneRule] = {}
    for quality, item in raw.items():
        if quality not in ("gold", "purple"):
            raise ValueError(f"{where} 仅支持 gold/purple，收到 {quality!r}")
        if not isinstance(item, dict):
            raise ValueError(f"{where}.{quality} 必须是字典")
        result[quality] = TuningStoneRule(
            tune_cost=_stone_map(
                item.get("tune_cost"), f"{where}.{quality}.tune_cost"),
            reset_refund=_stone_map(
                item.get("reset_refund"), f"{where}.{quality}.reset_refund"),
            recycle_refund=_stone_map(
                item.get("recycle_refund"),
                f"{where}.{quality}.recycle_refund"),
        )
    return result


def derive_chengyin_cap(cap) -> float:
    """按 94% 从等级上限推一个承音上限**初值**（一位小数，与游戏显示位数一致）。

    只用于两处：新建等级时先填一个默认值给填写者改，以及旧配置缺失
    ``chengyin`` 字段时的迁移回退。计算链路一律读配置原值，不调用它。
    """
    try:
        return round(float(cap) * _CHENGYIN_RATIO, 1)
    except (TypeError, ValueError):
        return 0.0
