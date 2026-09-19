"""燕云赛季类 Profile 周期边界（经 AppHooks.profile_period_modules 注册）。"""

from __future__ import annotations

from datetime import datetime, timedelta

from lvjiang.core.profile.periods import parse_reset_time, register_profile_period

from ..config.manager import SEASON_SWITCH_HOUR, get_game_config


def _season_boundary(
    reset_time: str,
    now: datetime,
    _reset_day: int,
    *,
    half: bool,
) -> datetime:
    hour, minute = parse_reset_time(reset_time)
    season = get_game_config().season_at(now)
    if season is None:
        raise ValueError(f"当前时间 {now} 不在任何赛季范围内")
    boundary = season.start_date
    if boundary is None:
        raise ValueError(f"赛季 {season.season_number} 缺少开始日期")
    if half and season.first_half_end_date:
        second_half_start = season.first_half_end_date + timedelta(days=1)
        switch_at = datetime.combine(
            second_half_start, datetime.min.time(),
        ).replace(hour=SEASON_SWITCH_HOUR)
        if now >= switch_at:
            boundary = second_half_start
    return datetime.combine(boundary, datetime.min.time()).replace(
        hour=hour,
        minute=minute,
    )


def resolve_season_boundary(
    reset_time: str,
    now: datetime,
    reset_day: int,
) -> datetime:
    return _season_boundary(reset_time, now, reset_day, half=False)


def resolve_half_season_boundary(
    reset_time: str,
    now: datetime,
    reset_day: int,
) -> datetime:
    return _season_boundary(reset_time, now, reset_day, half=True)


register_profile_period("season", resolve_season_boundary, label="赛季")
register_profile_period(
    "half_season",
    resolve_half_season_boundary,
    label="半赛季",
)
