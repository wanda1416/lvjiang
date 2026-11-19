"""设备在线判定：与网页端 `server/web/src/devices.ts` 同一口径。

心跳 10 分钟一跳、在线窗口 20 分钟（容两跳）。口径不一致会让同一条设备在手机
和网页上一边显示「在线」一边显示「离线」，用户没法判断到底是哪台设备在同步。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lvjiang.core.account.devices import (
    ONLINE_WINDOW_MINUTES,
    device_online,
    device_status_text,
    format_relative,
)

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)


def _ago(minutes: float) -> str:
    return (NOW - timedelta(minutes=minutes)).isoformat()


def test_online_window_is_twenty_minutes() -> None:
    assert ONLINE_WINDOW_MINUTES == 20
    assert device_online(_ago(19), now=NOW)
    assert not device_online(_ago(21), now=NOW)


def test_utc_suffix_timestamps_are_accepted() -> None:
    """服务端发的是 `...Z` 形式的 ISO 时间，解析不了就会把在线设备全报成离线。"""
    stamp = NOW.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    assert device_online(stamp, now=NOW)


def test_unparsable_time_counts_as_offline() -> None:
    for value in ("", "not-a-time", "—"):
        assert not device_online(value, now=NOW)
        assert format_relative(value, now=NOW) == "—"


def test_status_text_reads_online_or_last_seen() -> None:
    assert device_status_text(_ago(1), now=NOW) == "在线"
    assert device_status_text(_ago(35), now=NOW) == "最后在线 35 分钟前"
