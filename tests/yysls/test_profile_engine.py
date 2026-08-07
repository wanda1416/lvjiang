"""玩家数据模型后台引擎测试

覆盖 profile_engine.py 的纯逻辑函数：
- 周期边界计算
- 实时值计算
- profile 节点读写
- 提醒去重
"""

from datetime import datetime, timedelta

import pytest

from lvjiang.apps.yysls.config.user_profile import (
    read_profile_entry,
    write_profile_entry,
)
from lvjiang.apps.yysls.profile.profile_engine import (
    _compute_realtime_value,
    _get_period_boundary,
    _parse_reset_time,
    _should_reset,
)

# ─── _parse_reset_time ───────────────────────────────────────


class TestParseResetTime:
    def test_normal(self):
        assert _parse_reset_time("05:00") == (5, 0)
        assert _parse_reset_time("00:30") == (0, 30)
        assert _parse_reset_time("23:59") == (23, 59)

    def test_invalid(self):
        with pytest.raises(ValueError):
            _parse_reset_time("invalid")
        with pytest.raises((ValueError, IndexError)):
            _parse_reset_time("")


# ─── _get_period_boundary ────────────────────────────────────


class TestGetPeriodBoundary:
    def test_day_before_reset(self):
        """重置时刻之前 → 返回昨天的重置点"""
        now = datetime(2026, 8, 8, 3, 0)  # 03:00，在 05:00 之前
        boundary = _get_period_boundary("day", "05:00", now)
        assert boundary == datetime(2026, 8, 7, 5, 0)

    def test_day_after_reset(self):
        """重置时刻之后 → 返回今天的重置点"""
        now = datetime(2026, 8, 8, 10, 0)  # 10:00，在 05:00 之后
        boundary = _get_period_boundary("day", "05:00", now)
        assert boundary == datetime(2026, 8, 8, 5, 0)

    def test_day_at_reset(self):
        """恰好在重置时刻 → 返回今天的重置点"""
        now = datetime(2026, 8, 8, 5, 0)
        boundary = _get_period_boundary("day", "05:00", now)
        assert boundary == datetime(2026, 8, 8, 5, 0)

    def test_week_before_reset(self):
        """周一重置时刻之前 → 返回上周一的重置点"""
        # 2026-08-10 是周一
        now = datetime(2026, 8, 10, 3, 0)  # 周一 03:00
        boundary = _get_period_boundary("week", "05:00", now)
        expected = datetime(2026, 8, 3, 5, 0)  # 上周一
        assert boundary == expected

    def test_week_after_reset(self):
        """周一重置时刻之后 → 返回本周一的重置点"""
        now = datetime(2026, 8, 10, 10, 0)  # 周一 10:00
        boundary = _get_period_boundary("week", "05:00", now)
        expected = datetime(2026, 8, 10, 5, 0)  # 本周一
        assert boundary == expected

    def test_week_midweek(self):
        """周三 → 返回本周一的重置点"""
        now = datetime(2026, 8, 12, 15, 0)  # 周三 15:00
        boundary = _get_period_boundary("week", "05:00", now)
        expected = datetime(2026, 8, 10, 5, 0)  # 本周一
        assert boundary == expected

    def test_month_first_after_reset(self):
        """月初，重置时刻之后 → 返回本月 1 号"""
        now = datetime(2026, 8, 1, 10, 0)
        boundary = _get_period_boundary("month", "05:00", now)
        expected = datetime(2026, 8, 1, 5, 0)
        assert boundary == expected

    def test_month_first_before_reset(self):
        """月初 1 号，重置时刻之前 → 返回上月 1 号"""
        now = datetime(2026, 8, 1, 3, 0)
        boundary = _get_period_boundary("month", "05:00", now)
        expected = datetime(2026, 7, 1, 5, 0)
        assert boundary == expected

    def test_month_mid(self):
        """月中 → 返回本月 1 号"""
        now = datetime(2026, 8, 15, 12, 0)
        boundary = _get_period_boundary("month", "05:00", now)
        expected = datetime(2026, 8, 1, 5, 0)
        assert boundary == expected


# ─── _should_reset ───────────────────────────────────────────


class TestShouldReset:
    def test_empty_updated_at(self):
        assert _should_reset("", datetime.now()) is True

    def test_before_boundary(self):
        updated_at = (datetime.now() - timedelta(hours=2)).isoformat()
        boundary = datetime.now() - timedelta(hours=1)
        assert _should_reset(updated_at, boundary) is True

    def test_after_boundary(self):
        updated_at = datetime.now().isoformat()
        boundary = datetime.now() - timedelta(hours=1)
        assert _should_reset(updated_at, boundary) is False

    def test_invalid_format(self):
        assert _should_reset("not-a-date", datetime.now()) is True


# ─── _compute_realtime_value ─────────────────────────────────


class TestComputeRealtimeValue:
    def test_no_regen(self):
        """regen_rate=0 不回复"""
        result = _compute_realtime_value(100, "2026-08-08T10:00:00", 0.0, 2500)
        assert result == 100

    def test_regen_within_cap(self):
        """回复未封顶"""
        now = datetime.now()
        updated_at = (now - timedelta(minutes=60)).isoformat(timespec="seconds")
        # 60 分钟 * 0.125/min = 7.5
        result = _compute_realtime_value(100, updated_at, 0.125, 2500)
        assert result == 107  # int(100 + 7.5) = 107

    def test_regen_capped(self):
        """回复封顶"""
        now = datetime.now()
        updated_at = (now - timedelta(minutes=6000)).isoformat(timespec="seconds")
        # 大量时间后应封顶
        result = _compute_realtime_value(2400, updated_at, 0.125, 2500)
        assert result == 2500

    def test_no_updated_at(self):
        """无 updated_at 返回原始值"""
        result = _compute_realtime_value(100, "", 0.125, 2500)
        assert result == 100

    def test_negative_elapsed(self):
        """未来时间戳不回复"""
        future = (datetime.now() + timedelta(hours=1)).isoformat(timespec="seconds")
        result = _compute_realtime_value(100, future, 0.125, 2500)
        assert result == 100


# ─── profile 节点读写 ────────────────────────────────────────


class TestProfileEntry:
    def test_read_empty(self):
        data = {}
        assert read_profile_entry(data, "daily", "k") == {}

    def test_read_existing(self):
        data = {"profile": {"daily": {"k": {"value": 100, "updated_at": "2026-08-08T10:00:00"}}}}
        entry = read_profile_entry(data, "daily", "k")
        assert entry["value"] == 100

    def test_write_new(self):
        data = {}
        write_profile_entry(data, "daily", "k", 100)
        assert data["profile"]["daily"]["k"]["value"] == 100
        assert "updated_at" in data["profile"]["daily"]["k"]

    def test_write_with_total(self):
        data = {}
        write_profile_entry(data, "activity", "k", 500, total=2000)
        entry = data["profile"]["activity"]["k"]
        assert entry["value"] == 500
        assert entry["total"] == 2000

    def test_write_overwrites(self):
        data = {"profile": {"daily": {"k": {"value": 100}}}}
        write_profile_entry(data, "daily", "k", 200)
        assert data["profile"]["daily"]["k"]["value"] == 200

    def test_read_different_models(self):
        data = {
            "profile": {
                "daily": {"k": {"value": 1}},
                "realtime": {"k": {"value": 2}},
            }
        }
        assert read_profile_entry(data, "daily", "k")["value"] == 1
        assert read_profile_entry(data, "realtime", "k")["value"] == 2
