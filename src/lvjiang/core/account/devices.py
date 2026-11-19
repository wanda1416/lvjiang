"""设备在线判定与展示文案 —— 与网页端 ``server/web/src/devices.ts`` 同一口径。

心跳每 10 分钟一跳（PC 的 ``AccountHolder`` 与手机前台节拍都是），在线窗口取
20 分钟容两跳。判定只作展示：真正的授权状态由服务端 ``sessions`` / ``devices``
决定，「离线」只代表这段时间没来心跳，不代表令牌失效。
"""
from __future__ import annotations

from datetime import datetime, timezone

#: 心跳间隔（分钟）：客户端侧一拍一跳的口径
HEARTBEAT_MINUTES = 10
#: 在线窗口（分钟）：容两跳，与网页端一致
ONLINE_WINDOW_MINUTES = 2 * HEARTBEAT_MINUTES

_MINUTE = 60
_HOUR = 60 * _MINUTE
_DAY = 24 * _HOUR


def _parse(value: str) -> datetime | None:
    """服务端时间戳（ISO 8601，带 Z 或 +08:00）→ aware datetime。"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def device_online(last_seen: str, *, now: datetime | None = None) -> bool:
    """最近一次心跳是否落在在线窗口内；解析不出来按离线处理（宁可保守）。"""
    seen = _parse(last_seen)
    if seen is None:
        return False
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return (current - seen).total_seconds() < ONLINE_WINDOW_MINUTES * _MINUTE


def format_relative(value: str, *, now: datetime | None = None) -> str:
    """相对时间：刚刚 / 12 分钟前 / 3 小时前 / 2 天前 / 具体日期。"""
    seen = _parse(value)
    if seen is None:
        return "—"
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    seconds = (current - seen).total_seconds()
    if seconds < _MINUTE:
        return "刚刚"
    if seconds < _HOUR:
        return f"{int(seconds // _MINUTE)} 分钟前"
    if seconds < _DAY:
        return f"{int(seconds // _HOUR)} 小时前"
    if seconds < 30 * _DAY:
        return f"{int(seconds // _DAY)} 天前"
    return seen.date().isoformat()


def device_status_text(last_seen: str, *, now: datetime | None = None) -> str:
    """一行状态：``在线`` / ``最后在线 12 分钟前``。"""
    if device_online(last_seen, now=now):
        return "在线"
    return f"最后在线 {format_relative(last_seen, now=now)}"
