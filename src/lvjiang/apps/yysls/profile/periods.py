"""燕云赛季类 Profile 周期边界。"""

from __future__ import annotations

from datetime import datetime, timedelta

from lvjiang.core.profile.periods import parse_reset_time, register_profile_period

from ..config.manager import get_game_config


def _season_boundary(
    reset_time: str,
    now: datetime,
    _reset_day: int,
    *,
    half: bool,
) -> datetime:
    hour, minute = parse_reset_time(reset_time)
    today = now.date()
    for season in get_game_config().get_season_configs():
        if not season.start_date or not season.end_date:
            continue
        if not season.start_date <= today <= season.end_date:
            continue
        boundary = season.start_date
        if half and season.first_half_end_date and today > season.first_half_end_date:
            boundary = season.first_half_end_date + timedelta(days=1)
        return datetime.combine(boundary, datetime.min.time()).replace(
            hour=hour,
            minute=minute,
        )
    raise ValueError(f"当前日期 {today} 不在任何赛季范围内")


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
