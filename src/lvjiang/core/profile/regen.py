"""Regen 计算规则。

realtime 表示按速率连续恢复，时间单位只用于让配置可读。
boundary 表示经过准点边界才恢复。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from loguru import logger

from .models import RegenKeyDef

REGEN_REALTIME = "realtime"
REGEN_BOUNDARY = "boundary"
VALID_REGEN_TYPES = (REGEN_REALTIME, REGEN_BOUNDARY)
VALID_RATE_UNITS = ("minute", "hour", "day", "week")
VALID_BOUNDARY_PERIODS = ("minute", "hour", "day", "week")


@dataclass(frozen=True)
class RegenResult:
    value: float
    updated_at: str
    persisted: bool


def parse_reset_time(reset_time: str) -> tuple[int, int]:
    parts = reset_time.split(":")
    return int(parts[0]), int(parts[1])


def period_seconds(unit: str) -> int:
    if unit == "minute":
        return 60
    if unit == "hour":
        return 3600
    if unit == "day":
        return 86400
    if unit == "week":
        return 604800
    raise ValueError(f"非法时间单位: {unit!r}")


def is_realtime_regen(kd: RegenKeyDef) -> bool:
    return kd.regen_type == REGEN_REALTIME and kd.regen_rate_value > 0


def is_boundary_regen(kd: RegenKeyDef) -> bool:
    return kd.regen_type == REGEN_BOUNDARY and kd.regen_amount > 0


def _parse_entry_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def compute_realtime_value(
    stored_value: float,
    updated_at: str,
    kd: RegenKeyDef,
    now: datetime | None = None,
) -> float:
    if kd.regen_rate_value <= 0:
        return float(stored_value or 0)
    stored_ts = _parse_entry_time(updated_at)
    if stored_ts is None:
        return float(stored_value or 0)
    now = now or datetime.now()
    elapsed = max((now - stored_ts).total_seconds(), 0)
    computed = float(stored_value or 0) + elapsed / period_seconds(kd.regen_rate_unit) * kd.regen_rate_value
    if kd.cap is not None:
        computed = min(computed, kd.cap)
    return computed


def normalize_realtime_write(
    kd: RegenKeyDef,
    raw_value: float,
    now: datetime | None = None,
) -> tuple[float, str]:
    """把实时恢复写入规范化为整数 value + 回拨 updated_at。

    用户输入 120.5 表示当前显示值为 120，距离下一点已经走完 0.5 点进度。
    SQLite 只保存 120，并把 updated_at 回拨到相应时间。
    """
    now = now or datetime.now()
    whole = math.floor(float(raw_value))
    fraction = max(float(raw_value) - whole, 0.0)
    if kd.cap is not None:
        whole = min(whole, int(kd.cap))
        if whole >= int(kd.cap):
            fraction = 0.0
    if kd.regen_rate_value <= 0:
        return float(whole), now.isoformat(timespec="seconds")
    rollback = fraction * period_seconds(kd.regen_rate_unit) / kd.regen_rate_value
    updated_at = now - timedelta(seconds=rollback)
    return float(whole), updated_at.isoformat(timespec="seconds")


def next_realtime_point_seconds(entry: dict, kd: RegenKeyDef, now: datetime | None = None) -> float | None:
    current = compute_realtime_value(entry.get("value", 0) or 0, entry.get("updated_at", ""), kd, now)
    if kd.cap is not None and current >= kd.cap:
        return None
    fraction = current - math.floor(current)
    points_needed = 1.0 - fraction if fraction > 0 else 1.0
    return points_needed * period_seconds(kd.regen_rate_unit) / kd.regen_rate_value


def _day_boundary(dt: datetime, reset_time: str) -> datetime:
    hour, minute = parse_reset_time(reset_time)
    boundary = dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if dt >= boundary:
        return boundary
    return boundary - timedelta(days=1)


def _week_boundary(dt: datetime, reset_time: str, reset_day: int = 0) -> datetime:
    target_wd = reset_day if 1 <= reset_day <= 7 else 1
    target_idx = target_wd - 1
    hour, minute = parse_reset_time(reset_time)
    days_diff = dt.weekday() - target_idx
    boundary = (dt - timedelta(days=days_diff)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    if dt >= boundary:
        return boundary
    return boundary - timedelta(weeks=1)


def boundary_floor(dt: datetime, kd: RegenKeyDef) -> datetime:
    if kd.regen_period == "minute":
        return dt.replace(second=0, microsecond=0)
    if kd.regen_period == "hour":
        return dt.replace(minute=0, second=0, microsecond=0)
    if kd.regen_period == "day":
        return _day_boundary(dt, kd.reset_time)
    if kd.regen_period == "week":
        return _week_boundary(dt, kd.reset_time, kd.reset_day)
    raise ValueError(f"非法准点周期: {kd.regen_period!r}")


def next_boundary_after(dt: datetime, kd: RegenKeyDef) -> datetime:
    current = boundary_floor(dt, kd)
    if kd.regen_period == "minute":
        return current + timedelta(minutes=1)
    if kd.regen_period == "hour":
        return current + timedelta(hours=1)
    if kd.regen_period == "day":
        return current + timedelta(days=1)
    if kd.regen_period == "week":
        return current + timedelta(weeks=1)
    raise ValueError(f"非法准点周期: {kd.regen_period!r}")


def _count_boundaries(prev: datetime, now: datetime, kd: RegenKeyDef) -> tuple[int, datetime]:
    if now <= prev:
        return 0, prev
    first = next_boundary_after(prev, kd)
    if first > now:
        return 0, prev
    if kd.regen_period == "minute":
        count = int((boundary_floor(now, kd) - first).total_seconds() // 60) + 1
    elif kd.regen_period == "hour":
        count = int((boundary_floor(now, kd) - first).total_seconds() // 3600) + 1
    elif kd.regen_period == "day":
        count = (boundary_floor(now, kd).date() - first.date()).days + 1
    elif kd.regen_period == "week":
        count = int((boundary_floor(now, kd) - first).total_seconds() // 604800) + 1
    else:
        raise ValueError(f"非法准点周期: {kd.regen_period!r}")
    last = first
    for _ in range(max(count - 1, 0)):
        last = next_boundary_after(last, kd)
    return max(count, 0), last


def compute_boundary_value(
    stored_value: float,
    updated_at: str,
    kd: RegenKeyDef,
    now: datetime | None = None,
) -> tuple[float, str]:
    if kd.regen_amount <= 0:
        return float(stored_value or 0), updated_at
    stored_ts = _parse_entry_time(updated_at)
    if stored_ts is None:
        return float(stored_value or 0), updated_at
    now = now or datetime.now()
    count, last_boundary = _count_boundaries(stored_ts, now, kd)
    computed = float(stored_value or 0) + count * kd.regen_amount
    if kd.cap is not None:
        computed = min(computed, kd.cap)
    new_ts = last_boundary.isoformat(timespec="seconds") if count else updated_at
    return computed, new_ts


def compute_regen_entry(entry: dict, kd: RegenKeyDef, now: datetime | None = None) -> RegenResult:
    stored_value = entry.get("value", 0) or 0
    updated_at = entry.get("updated_at", "")
    if kd.regen_type == REGEN_REALTIME:
        value = compute_realtime_value(stored_value, updated_at, kd, now)
        return RegenResult(value=value, updated_at=updated_at, persisted=False)
    if kd.regen_type == REGEN_BOUNDARY:
        value, new_ts = compute_boundary_value(stored_value, updated_at, kd, now)
        return RegenResult(value=value, updated_at=new_ts, persisted=True)
    logger.error(f"非法 regen_type={kd.regen_type!r}，不计算回复")
    return RegenResult(value=float(stored_value or 0), updated_at=updated_at, persisted=False)


def format_seconds(seconds: float) -> str:
    seconds_i = max(int(math.ceil(seconds)), 0)
    days, rem = divmod(seconds_i, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}天")
    if hours:
        parts.append(f"{hours}小时")
    if minutes:
        parts.append(f"{minutes}分")
    if secs or not parts:
        parts.append(f"{secs}秒")
    return "".join(parts)
