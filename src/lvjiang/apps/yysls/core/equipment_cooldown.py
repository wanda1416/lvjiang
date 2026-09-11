"""装备重置冷却时间的计算规则。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _parse_expiry(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        expiry = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry.astimezone(timezone.utc)


def next_cooldown_expiry(
    previous_expires_at: object,
    *,
    days: int,
    carryover: bool,
    now: datetime | None = None,
) -> str:
    """计算一次重置后的截止时间，最多保留一次已到期的重置次数。

    开启累计后，旧冷却若已到期，新一轮从旧截止时间继续计算。旧冷却
    逾期超过一轮时只结转到当前时刻，因此最多允许紧接着再重置一次。
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    duration = timedelta(days=days)
    expires_at = current + duration

    previous = _parse_expiry(previous_expires_at)
    if carryover and previous is not None and previous <= current:
        expires_at = max(current, previous + duration)

    return expires_at.isoformat(timespec="milliseconds")
