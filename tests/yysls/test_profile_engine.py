"""玩家数据模型后台引擎测试

覆盖 profile_engine.py 的纯逻辑函数：
- 周期边界计算
- 实时值计算
- profile 节点读写
- 提醒去重
"""

from datetime import datetime, timedelta

import pytest

from lvjiang.apps.yysls.core.profile_engine.profile_engine import (
    _compute_regen_value,
    _count_quota_regens,
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


class TestGetPeriodBoundaryResetDay:
    """reset_day 参数测试"""

    def test_week_friday_after_reset(self):
        """周五重置：当前在周五 05:00 之后 → 返回本周五"""
        # 2026-08-14 是周五
        now = datetime(2026, 8, 14, 10, 0)  # 周五 10:00
        boundary = _get_period_boundary("week", "05:00", now, reset_day=5)
        expected = datetime(2026, 8, 14, 5, 0)  # 本周五
        assert boundary == expected

    def test_week_friday_before_reset(self):
        """周五重置：当前在周五 05:00 之前 → 返回上周五"""
        now = datetime(2026, 8, 14, 3, 0)  # 周五 03:00
        boundary = _get_period_boundary("week", "05:00", now, reset_day=5)
        expected = datetime(2026, 8, 7, 5, 0)  # 上周五
        assert boundary == expected

    def test_week_friday_on_wednesday(self):
        """周五重置：当前在周三 → 返回上周五"""
        # 2026-08-12 是周三
        now = datetime(2026, 8, 12, 15, 0)  # 周三 15:00
        boundary = _get_period_boundary("week", "05:00", now, reset_day=5)
        expected = datetime(2026, 8, 7, 5, 0)  # 上周五
        assert boundary == expected

    def test_week_sunday(self):
        """周日重置：reset_day=7"""
        # 2026-08-16 是周日
        now = datetime(2026, 8, 16, 10, 0)  # 周日 10:00
        boundary = _get_period_boundary("week", "05:00", now, reset_day=7)
        expected = datetime(2026, 8, 16, 5, 0)  # 本周日
        assert boundary == expected

    def test_week_default_is_monday(self):
        """reset_day=0 默认周一"""
        now = datetime(2026, 8, 12, 15, 0)  # 周三
        boundary_default = _get_period_boundary("week", "05:00", now, reset_day=0)
        boundary_monday = _get_period_boundary("week", "05:00", now, reset_day=1)
        assert boundary_default == boundary_monday

    def test_month_15th_after_reset(self):
        """月重置 15 号：当前在 15 号 05:00 之后 → 返回本月 15 号"""
        now = datetime(2026, 8, 15, 10, 0)
        boundary = _get_period_boundary("month", "05:00", now, reset_day=15)
        expected = datetime(2026, 8, 15, 5, 0)
        assert boundary == expected

    def test_month_15th_before_reset(self):
        """月重置 15 号：当前在 15 号 05:00 之前 → 返回上月 15 号"""
        now = datetime(2026, 8, 15, 3, 0)
        boundary = _get_period_boundary("month", "05:00", now, reset_day=15)
        expected = datetime(2026, 7, 15, 5, 0)
        assert boundary == expected

    def test_month_15th_on_10th(self):
        """月重置 15 号：当前在 10 号 → 返回上月 15 号"""
        now = datetime(2026, 8, 10, 12, 0)
        boundary = _get_period_boundary("month", "05:00", now, reset_day=15)
        expected = datetime(2026, 7, 15, 5, 0)
        assert boundary == expected

    def test_month_default_is_1st(self):
        """reset_day=0 默认 1 号"""
        now = datetime(2026, 8, 15, 12, 0)
        boundary_default = _get_period_boundary("month", "05:00", now, reset_day=0)
        boundary_1st = _get_period_boundary("month", "05:00", now, reset_day=1)
        assert boundary_default == boundary_1st


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


# ─── _count_quota_regens ────────────────────────────────────────


class TestCountDailyRegens:
    def test_same_day_before_reset(self):
        """同一天，都在 05:00 之前 → 0"""
        prev = datetime(2026, 8, 8, 3, 0)
        now = datetime(2026, 8, 8, 4, 59)
        assert _count_quota_regens(prev, now, "05:00") == 0

    def test_same_day_after_reset(self):
        """同一天，都在 05:00 之后 → 0"""
        prev = datetime(2026, 8, 8, 6, 0)
        now = datetime(2026, 8, 8, 10, 0)
        assert _count_quota_regens(prev, now, "05:00") == 0

    def test_same_day_cross_reset(self):
        """同一天 05:00 前 → 05:00 后，应计入今天重置点"""
        prev = datetime(2026, 8, 8, 4, 40)
        now = datetime(2026, 8, 8, 5, 9)
        assert _count_quota_regens(prev, now, "05:00") == 1

    def test_cross_one_reset(self):
        """昨天 03:00 → 今天 10:00，经过昨天和今天两个 05:00 → 2"""
        prev = datetime(2026, 8, 7, 3, 0)
        now = datetime(2026, 8, 8, 10, 0)
        assert _count_quota_regens(prev, now, "05:00") == 2

    def test_cross_two_resets(self):
        """前天 20:00 → 今天 10:00，经过昨天和今天两个 05:00 → 2"""
        prev = datetime(2026, 8, 6, 20, 0)
        now = datetime(2026, 8, 8, 10, 0)
        assert _count_quota_regens(prev, now, "05:00") == 2

    def test_prev_after_today_reset(self):
        """prev 在今天 05:00 之后，now 也在今天 → 0"""
        prev = datetime(2026, 8, 8, 8, 0)
        now = datetime(2026, 8, 8, 12, 0)
        assert _count_quota_regens(prev, now, "05:00") == 0

    def test_cross_reset_with_different_time(self):
        """reset_time=00:30 的跨天场景"""
        prev = datetime(2026, 8, 7, 23, 0)
        now = datetime(2026, 8, 8, 1, 0)
        assert _count_quota_regens(prev, now, "00:30") == 1

    def test_cross_month_boundary(self):
        """跨月：1月31日 20:00 → 2月2日 10:00，经过 2 个 05:00 → 2"""
        prev = datetime(2026, 1, 31, 20, 0)
        now = datetime(2026, 2, 2, 10, 0)
        assert _count_quota_regens(prev, now, "05:00") == 2

    def test_now_before_today_reset(self):
        """now 在今天 05:00 之前，今天重置尚未到达，不应计入"""
        # 前天 03:00 → 今天 03:00，经过前天和昨天两个 05:00 边界
        prev = datetime(2026, 8, 6, 3, 0)
        now = datetime(2026, 8, 8, 3, 0)
        assert _count_quota_regens(prev, now, "05:00") == 2

    def test_now_before_today_reset_one_day(self):
        """昨天 03:00 → 今天 03:00，今天 05:00 还没到，但昨天 05:00 已过 → 1"""
        prev = datetime(2026, 8, 7, 3, 0)
        now = datetime(2026, 8, 8, 3, 0)
        assert _count_quota_regens(prev, now, "05:00") == 1


# ─── _compute_regen_value ─────────────────────────────────


class TestComputeRealtimeValue:
    def test_no_regen(self):
        """regen_value=0 不回复"""
        val, ts = _compute_regen_value(100, "2026-08-08T10:00:00", "minute", 0.0, 2500)
        assert val == 100
        assert ts == "2026-08-08T10:00:00"

    def test_minute_regen(self):
        """分钟回复：60 分钟 * 0.125 = 7.5"""
        now = datetime.now()
        updated_at = (now - timedelta(minutes=60)).isoformat(timespec="seconds")
        val, new_ts = _compute_regen_value(100, updated_at, "minute", 0.125, 2500)
        assert val == 107.5  # 100 + 60 * 0.125

    def test_hour_regen(self):
        """小时回复：2 小时 * 10 = 20"""
        now = datetime.now()
        updated_at = (now - timedelta(minutes=150)).isoformat(timespec="seconds")
        val, new_ts = _compute_regen_value(100, updated_at, "hour", 10, 2500)
        # 150 分钟 = 2 整小时
        assert val == 120  # 100 + 2 * 10

    def test_day_regen(self):
        """天回复：跨越 05:00 边界 * 450"""
        from unittest.mock import patch
        # 使用固定的 now 时间（10:00，确保在 05:00 之后）
        fixed_now = datetime(2026, 8, 8, 10, 0, 0)
        yesterday_3am = datetime(2026, 8, 7, 3, 0, 0)
        updated_at = yesterday_3am.isoformat(timespec="seconds")
        with patch('lvjiang.apps.yysls.core.profile_engine.profile_engine.datetime') as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.fromisoformat = datetime.fromisoformat
            val, ts = _compute_regen_value(100, updated_at, "day", 450, 2500, "05:00")
        # Should have crossed at least 1 day boundary
        assert val >= 550  # 100 + 1 * 450

    def test_day_regen_same_day_cross_reset(self):
        """体力：04:40 → 05:09 应跨过 05:00 并增加 450"""
        from unittest.mock import patch
        fixed_now = datetime(2026, 8, 9, 5, 9, 0)
        updated_at = datetime(2026, 8, 9, 4, 40, 6).isoformat(timespec="seconds")
        with patch('lvjiang.apps.yysls.core.profile_engine.profile_engine.datetime') as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.fromisoformat = datetime.fromisoformat
            val, ts = _compute_regen_value(1256, updated_at, "day", 450, 2500, "05:00")
        assert val == 1706
        assert ts == "2026-08-09T05:09:00"

    def test_regen_capped(self):
        """回复封顶"""
        now = datetime.now()
        updated_at = (now - timedelta(minutes=6000)).isoformat(timespec="seconds")
        val, _ = _compute_regen_value(2400, updated_at, "minute", 0.125, 2500)
        assert val == 2500

    def test_no_updated_at(self):
        """无 updated_at 返回原始值"""
        val, ts = _compute_regen_value(100, "", "minute", 0.125, 2500)
        assert val == 100
        assert ts == ""

    def test_negative_elapsed(self):
        """未来时间戳不回复"""
        future = (datetime.now() + timedelta(hours=1)).isoformat(timespec="seconds")
        val, ts = _compute_regen_value(100, future, "minute", 0.125, 2500)
        assert val == 100

    def test_minute_alignment(self):
        """整分钟对齐：秒数误差不累积"""
        now = datetime.now()
        updated_at = (now - timedelta(minutes=5, seconds=20)).isoformat(timespec="seconds")
        val, new_ts = _compute_regen_value(100, updated_at, "minute", 1.0, None)
        # 5 整分钟 * 1.0 = 5
        assert val == 105.0

    def test_fractional_progress_is_preserved(self):
        """小数表示已累计进度：100.5 再过 4 分钟变 101.0"""
        from unittest.mock import patch

        fixed_now = datetime(2026, 8, 9, 10, 4, 0)
        updated_at = datetime(2026, 8, 9, 10, 0, 0).isoformat(timespec="seconds")
        with patch('lvjiang.apps.yysls.core.profile_engine.profile_engine.datetime') as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.fromisoformat = datetime.fromisoformat
            val, new_ts = _compute_regen_value(
                100.5, updated_at, "minute", 0.125, 600
            )

        assert val == 101.0
        assert new_ts == "2026-08-09T10:04:00"

    def test_fractional_progress_multi_tick_matches_single_tick(self):
        """多轮 tick 与一次性长时间 tick 的结果一致，不累计截断误差"""
        from unittest.mock import patch

        start = datetime(2026, 8, 9, 10, 0, 0)
        first_now = datetime(2026, 8, 9, 10, 3, 30)
        second_now = datetime(2026, 8, 9, 10, 8, 0)
        updated_at = start.isoformat(timespec="seconds")

        with patch('lvjiang.apps.yysls.core.profile_engine.profile_engine.datetime') as mock_dt:
            mock_dt.now.return_value = first_now
            mock_dt.fromisoformat = datetime.fromisoformat
            val1, ts1 = _compute_regen_value(
                100.5, updated_at, "minute", 0.125, 600
            )

        with patch('lvjiang.apps.yysls.core.profile_engine.profile_engine.datetime') as mock_dt:
            mock_dt.now.return_value = second_now
            mock_dt.fromisoformat = datetime.fromisoformat
            stepped_val, stepped_ts = _compute_regen_value(
                val1, ts1, "minute", 0.125, 600
            )
            single_val, single_ts = _compute_regen_value(
                100.5, updated_at, "minute", 0.125, 600
            )

        assert stepped_val == single_val == 101.5
        assert stepped_ts == single_ts == "2026-08-09T10:08:00"

    def test_sub_minute_progress_does_not_advance_timestamp(self):
        """未满一个 minute 周期时，不推进 updated_at，剩余秒数保留在时间差里"""
        from unittest.mock import patch

        fixed_now = datetime(2026, 8, 9, 10, 0, 59)
        updated_at = datetime(2026, 8, 9, 10, 0, 0).isoformat(timespec="seconds")
        with patch('lvjiang.apps.yysls.core.profile_engine.profile_engine.datetime') as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.fromisoformat = datetime.fromisoformat
            val, new_ts = _compute_regen_value(
                100.5, updated_at, "minute", 0.125, 600
            )

        assert val == 100.5
        assert new_ts == updated_at
