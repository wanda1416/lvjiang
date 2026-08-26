"""metrics_user 的查询口径 + analysis_bridge 对 telemetry_analysis 的复用，
用手搭的最小 sqlite 库验证——对齐 ops/stats-worker/queries/rollup.sql
"直接对 sqlite 跑验证" 的先例。"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stats_client import database, metrics_user  # noqa: E402
from stats_client.analysis_bridge import (  # noqa: E402
    build_report_from_cache,
    load_cached_events,
)


def _today_utc8() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")


def _seed_daily(conn, day, install_id, first_day=None, **overrides):
    row = {"day": day, "install_id": install_id, "first_day": first_day or day,
           "app_version": "0.7.0", "run_env": "desktop", "os_name": "windows",
           "os_release": "11", "arch": "x64", "ui_lang": "zh-CN", "plugin": "yysls"}
    row.update(overrides)
    conn.execute(
        "INSERT INTO remote_daily (day, install_id, first_day, app_version, run_env, "
        "os_name, os_release, arch, ui_lang, plugin) VALUES "
        "(:day, :install_id, :first_day, :app_version, :run_env, :os_name, "
        ":os_release, :arch, :ui_lang, :plugin)", row)


def _seed_batch(conn, batch_id, day, received_at, events, install_id="i1"):
    conn.execute(
        "INSERT INTO remote_roll_batch (batch_id, install_id, day, app_version, "
        "plugin, n_events, payload, received_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (batch_id, install_id, day, "0.7.0", "yysls", len(events),
         json.dumps(events), received_at))


def _tuning_event(part="weapon", food="none", affix="会心", n_rolls=3,
                   install_id="i1", date=None):
    return {
        "schema": "yysls.tuning_session", "install_id": install_id,
        "date": date or _today_utc8(), "part": part, "weapon_type": "sword",
        "level": 80, "quality": "orange", "mode": "normal", "active_rule": "default",
        "season": "s1", "game_config_customized": False,
        "initial_affixes": [{"affix": affix, "cap_pct": 10.0, "is_transferred": False}],
        "rolls": [{"affix": affix, "cap_pct": 20.0, "is_transferred": False,
                   "slot": 1, "food": food, "resets": 0} for _ in range(n_rolls)],
        "stop_reason": "hit_target", "final_rating": "top",
        "total_rounds": n_rolls, "resets": 0,
    }


class TestMetricsUser:
    def test_overview_counts(self, tmp_path):
        conn = database.connect(tmp_path / "cache.sqlite3")
        today = _today_utc8()
        _seed_daily(conn, today, "i1")
        _seed_daily(conn, today, "i2")
        conn.execute(
            "INSERT INTO metric_daily (day, dau, new_installs, roll_sessions, "
            "roll_rounds, computed_at) VALUES (?, 2, 1, 3, 9, 'x')", (today,))
        conn.execute(
            "INSERT INTO remote_scalar (key, value, updated_at) VALUES "
            "('installs_count', '42', 'x')")
        ov = metrics_user.overview(conn)
        assert ov["dau_today"] == 2
        assert ov["installs_count_remote_180d"] == 42
        assert ov["installs_count_local"] == 2

    def test_version_and_platform_dist(self, tmp_path):
        conn = database.connect(tmp_path / "cache.sqlite3")
        today = _today_utc8()
        _seed_daily(conn, today, "i1", app_version="0.7.0")
        _seed_daily(conn, today, "i2", app_version="0.6.5")
        _seed_daily(conn, today, "i3", app_version="0.7.0")
        dist = metrics_user.version_dist(conn)
        assert dist[0] == {"app_version": "0.7.0", "n": 2}

    def test_retention_cohort_drops_small_cohorts(self, tmp_path):
        conn = database.connect(tmp_path / "cache.sqlite3")
        today = _today_utc8()
        # cohort 只有 2 人，应该被 min_cohort_n=20 挡掉
        _seed_daily(conn, today, "i1", first_day=today)
        _seed_daily(conn, today, "i2", first_day=today)
        rows = metrics_user.retention_cohort(conn, min_cohort_n=20)
        assert rows == []
        rows = metrics_user.retention_cohort(conn, min_cohort_n=1)
        assert len(rows) == 1


class TestAnalysisBridge:
    def test_load_cached_events_roundtrips_through_loader(self, tmp_path):
        conn = database.connect(tmp_path / "cache.sqlite3")
        _seed_batch(conn, "b1", _today_utc8(), "2026-08-20T00:00:00Z",
                    [_tuning_event(), _tuning_event(affix="攻击")])
        events = load_cached_events(conn)
        assert len(events) == 2
        assert events[0]["part"] == "weapon"

    def test_build_report_from_cache_reuses_telemetry_analysis(self, tmp_path):
        conn = database.connect(tmp_path / "cache.sqlite3")
        events = [_tuning_event(affix="会心", n_rolls=5, install_id=f"i{i}")
                  for i in range(40)]
        _seed_batch(conn, "b1", _today_utc8(), "2026-08-20T00:00:00Z", events)
        report = build_report_from_cache(conn, top=5)
        assert report.n_events == 40
        assert report.n_rolls == 200
        assert "词条分布" in report.text
        assert "会心" in report.text

    def test_build_report_from_cache_empty_raises(self, tmp_path):
        import pytest
        conn = database.connect(tmp_path / "cache.sqlite3")
        with pytest.raises(ValueError):
            build_report_from_cache(conn)
