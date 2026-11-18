"""Profile 周期边界注册表。

主引擎内置日、周、月三种通用边界；插件可注册额外的业务周期。
周期名位于全局共享命名空间，重复注册直接报错，禁止静默覆盖。
"""

from __future__ import annotations

import calendar
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

ProfilePeriodResolver = Callable[[str, datetime, int], datetime]


@dataclass(frozen=True)
class ProfilePeriod:
    name: str
    label: str
    resolver: ProfilePeriodResolver


_PERIODS: dict[str, ProfilePeriod] = {}


def register_profile_period(
    name: str,
    resolver: ProfilePeriodResolver,
    *,
    label: str | None = None,
) -> None:
    """注册 Profile 周期边界解析器。"""
    key = name.strip()
    if not key:
        raise ValueError("Profile 周期名不能为空")
    if key in _PERIODS:
        raise ValueError(f"Profile 周期 {key!r} 已注册")
    _PERIODS[key] = ProfilePeriod(key, label or key, resolver)


def get_profile_period(name: str) -> ProfilePeriod | None:
    return _PERIODS.get(name)


def list_profile_periods() -> tuple[ProfilePeriod, ...]:
    return tuple(_PERIODS.values())


def get_period_boundary(
    period: str,
    reset_time: str,
    now: datetime,
    reset_day: int = 0,
) -> datetime:
    definition = get_profile_period(period)
    if definition is None:
        raise ValueError(f"未注册的 Profile 周期: {period!r}")
    return definition.resolver(reset_time, now, reset_day)


def parse_reset_time(reset_time: str) -> tuple[int, int]:
    parts = reset_time.split(":")
    return int(parts[0]), int(parts[1])


def quota_current_value(
    entry,
    kd,
    *,
    now: datetime | None = None,
) -> float:
    """quota 的当前有效值：``event_at`` 早于本周期边界就读作 0。

    这是**读时归零的唯一入口**：用户总览、DSL ``profile_get`` / ``profile_all``、
    Android 工作台、写入前的增量基线都走这里。它只算不写——库里保留上次真实
    写入的值与时间戳，归零在真实写入时才由
    :func:`lvjiang.core.profile.service._materialize_period_reset` 固化成一条
    ``reset`` 行。所以「周一 05:00:00 归零」不依赖引擎是否在跑、设备是否在线、
    读多少次，也不需要任何后台写入。

    ``entry=None``（条目不存在）视为 0 值、无时间戳；时间戳缺失或解析不出来
    时返回存储值——那种行是占位/迁移残留，判不出周期，凭空清零只会把数据
    藏起来。周期名未注册时同样返回存储值。
    """
    stored = float((getattr(entry, "value", None) or 0) if entry is not None else 0)
    event_at = ((getattr(entry, "event_at", "") or "") if entry is not None else "")
    from .arbitrate import parse_event

    event = parse_event(event_at)
    if event is None:
        return stored
    try:
        boundary = get_period_boundary(
            kd.period, kd.reset_time, now or datetime.now(),
            getattr(kd, "reset_day", 0),
        )
    except (AttributeError, TypeError, ValueError):
        return stored
    return 0.0 if event < boundary else stored


def next_period_boundary(
    period: str,
    reset_time: str,
    now: datetime,
    reset_day: int = 0,
) -> datetime | None:
    """``now`` 之后的下一个周期边界时刻；周期解析器不可用时返回 ``None``。

    刻意不内置「一天/一周/一个月」的换算：周期由插件注册，边界语义只有解析器
    自己知道。这里对「当前边界 + 探测步长」反复求解，步长翻倍直到解出比当前
    边界更晚的时刻——对任意单调解析器都成立，自定义周期（如燕云的
    season / half_season）也不需要各写一份规则。
    """
    try:
        current = get_period_boundary(period, reset_time, now, reset_day)
    except (TypeError, ValueError):
        return None
    probe = timedelta(seconds=1)
    while probe <= timedelta(days=400):
        try:
            candidate = get_period_boundary(
                period, reset_time, current + probe, reset_day,
            )
        except (TypeError, ValueError):
            return None
        if candidate > current:
            return candidate
        probe *= 2
    return None


def _day_boundary(reset_time: str, now: datetime, _reset_day: int) -> datetime:
    hour, minute = parse_reset_time(reset_time)
    reset_today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return reset_today if now >= reset_today else reset_today - timedelta(days=1)


def _week_boundary(reset_time: str, now: datetime, reset_day: int) -> datetime:
    hour, minute = parse_reset_time(reset_time)
    target_wd = reset_day if 1 <= reset_day <= 7 else 1
    days_diff = now.isoweekday() - target_wd
    reset_date = now.date() - timedelta(days=days_diff)
    reset_dt = datetime.combine(reset_date, datetime.min.time()).replace(
        hour=hour, minute=minute
    )
    return reset_dt if now >= reset_dt else reset_dt - timedelta(weeks=1)


def _month_boundary(reset_time: str, now: datetime, reset_day: int) -> datetime:
    hour, minute = parse_reset_time(reset_time)
    target_day = reset_day if 1 <= reset_day <= 31 else 1
    this_month_day = min(target_day, calendar.monthrange(now.year, now.month)[1])
    reset_this_month = now.replace(
        day=this_month_day,
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )
    if now >= reset_this_month:
        return reset_this_month

    if now.month == 1:
        previous_year, previous_month = now.year - 1, 12
    else:
        previous_year, previous_month = now.year, now.month - 1
    previous_day = min(
        target_day,
        calendar.monthrange(previous_year, previous_month)[1],
    )
    return now.replace(
        year=previous_year,
        month=previous_month,
        day=previous_day,
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )


register_profile_period("day", _day_boundary, label="每天")
register_profile_period("week", _week_boundary, label="每周")
register_profile_period("month", _month_boundary, label="每月")
