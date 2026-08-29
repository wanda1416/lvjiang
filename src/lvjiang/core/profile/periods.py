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
