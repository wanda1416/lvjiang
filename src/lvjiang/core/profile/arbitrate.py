"""Profile 当前值仲裁。

比较的是真实事件时间和 ``op_id``。不按字符串比较时间，避免无时区秒串和带偏移的
字符串被当成两个时刻。空 ``op_id`` 在时间相同的时候输给任何非空身份。
"""

from __future__ import annotations

from datetime import datetime


def parse_event(value: str | None) -> datetime | None:
    """解析本地秒串。带 ``Z`` 或数字偏移时只保留钟面，不换时区。"""
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1]
    if len(text) >= 20 and text[19] in "+-":
        text = text[:19]
    if len(text) > 19:
        text = text[:19]
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def event_rank(event_at: str | None, op_id: str | None) -> tuple:
    """排序键。没有合法时间的候选比任何有时间的候选都旧。"""
    parsed = parse_event(event_at)
    return (parsed is not None, parsed or datetime.min, op_id or "")


def canonical_event(value: str | None) -> str:
    """收成无时区的本地秒串。解析不了就返回空串，调用方不要把原串写进库。"""
    parsed = parse_event(value)
    if parsed is None:
        return ""
    return parsed.isoformat(timespec="seconds")


def is_strictly_newer(
    event_at: str | None,
    op_id: str | None,
    other_at: str | None,
    other_id: str | None,
) -> bool:
    """``(event_at, op_id)`` 是否严格新于另一个候选。"""
    return event_rank(event_at, op_id) > event_rank(other_at, other_id)
