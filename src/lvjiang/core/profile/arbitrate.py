"""Profile 当前值仲裁。

比较的是真实事件时间和 ``op_id``。不按字符串比较时间，避免无时区秒串和带偏移的
字符串被当成两个时刻。空 ``op_id`` 在时间相同的时候输给任何非空身份。

回放（:func:`is_newer_write`）不用 ``op_id`` 分先后：那是随机 uuid，不携带顺序。
事件时间相同时比云端到达序（``remote_id``），比不出来就不覆盖。
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


def is_newer_write(
    event_at: str | None,
    seq: int | None,
    other_at: str | None,
    other_seq: int | None,
) -> bool:
    """回放用的仲裁：顺序信号只有两个——事件时间、云端到达序（``remote_id``）。

    账本时间戳只到秒，而一次操作会连写两行——先把「上次写入至今的恢复」物化成
    ``regen`` 行，再写用户的 ``set``——两行落在同一秒里。旧实现拿 ``op_id`` 给
    这一秒分先后，可那是 ``uuid4``：2026-11-17 刁刁蓝心力 172 被前一条 regen 值
    532 顶掉、速行一 23 被顶成 383，全是 regen 行的 uuid 恰好排在后面，回放把
    用户紧随其后的 set 判成「不更新」直接丢了。``op_id`` 只标识「是不是同一条
    操作」，**不携带任何顺序**，所以这里根本不看它。

    同秒又拿不到到达序（本机未上传的行、快照条目）时判不出先后，就不覆盖现值：
    下一次观测写回的绝对真值会修正，账本按观测自愈。凭空挑一个赢家只会让两台
    设备各挑一边。
    """
    here = parse_event(event_at)
    there = parse_event(other_at)
    if there is None:
        # 现值没有事件时间：占位行 / 存量行，没有可比的先后，照旧落地。
        return True
    if here is None:
        # 来的一条没有时间，比有时间的现值旧。
        return False
    if here != there:
        return here > there
    if seq is None or other_seq is None:
        return False
    return seq > other_seq
