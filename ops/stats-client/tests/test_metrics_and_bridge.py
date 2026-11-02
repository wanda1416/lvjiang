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

    def test_retention_matrix_drops_small_cohorts_sorts_newest(self, tmp_path):
        """规模 < min_cohort_n 的周整行不出现；行按安装周倒序（新的在上）。"""
        conn = database.connect(tmp_path / "cache.sqlite3")
        older, newer = "2026-08-31", "2026-09-07"   # 两个周一，属相邻两周
        for index in range(20):
            _seed_daily(conn, older, f"old{index}", first_day=older)
            _seed_daily(conn, newer, f"new{index}", first_day=newer)

        assert metrics_user.retention_matrix(conn, min_cohort_n=21) == []

        rows = metrics_user.retention_matrix(conn, min_cohort_n=20)
        assert [r["week_start"] for r in rows] == [newer, older]
        assert [r["size"] for r in rows] == [20, 20]
        assert rows[0]["range"] == "09/07 -> 09/13"
        assert rows[1]["range"] == "08/31 -> 09/06"
        # 标签里的周号必须与分组键一致
        assert rows[0]["label"].startswith(rows[0]["cohort_week"] + " 周")

    def test_retention_matrix_milestones_and_cumulative(self, tmp_path):
        """D n 是恰好第 n 天；7 日累计只算第 1..7 天，不含安装当天与更远的天。"""
        conn = database.connect(tmp_path / "cache.sqlite3")
        first_day = "2026-09-07"                     # 周一
        def day_at(offset: int) -> str:
            return (datetime.fromisoformat(first_day)
                    + timedelta(days=offset)).strftime("%Y-%m-%d")

        for index in range(20):                      # 规模 = 20（装当天活跃）
            _seed_daily(conn, first_day, f"u{index}", first_day=first_day)
        for index in range(4):                       # 第 1 天回来 4 人
            _seed_daily(conn, day_at(1), f"u{index}", first_day=first_day)
        for index in range(2):                       # 第 3 天回来 2 人
            _seed_daily(conn, day_at(3), f"u{index}", first_day=first_day)
        _seed_daily(conn, day_at(7), "u0", first_day=first_day)   # 第 7 天回来 1 人
        # 第 8 天与第 26 天：不进 7 日累计，也不该自己变成一行
        _seed_daily(conn, day_at(8), "u19", first_day=first_day)
        _seed_daily(conn, day_at(26), "u18", first_day=first_day)

        rows = metrics_user.retention_matrix(conn, min_cohort_n=20)
        assert len(rows) == 1
        row = rows[0]
        assert row["size"] == 20
        cells = {c["day"]: c for c in row["cells"]}
        assert cells[1]["users"] == 4 and cells[1]["pct"] == 20.0
        assert cells[3]["users"] == 2 and cells[3]["pct"] == 10.0
        assert cells[7]["users"] == 1
        assert cells[14]["users"] == 0
        # 热度按 50% 封顶折成 0..1，供页面铺色（20% → 0.4，10% → 0.2）
        assert cells[1]["heat"] == 0.4
        assert cells[3]["heat"] == 0.2
        # 累计 = u0..u3（第 1 天）∪ 第 3 天 ∪ 第 7 天 = 4 人，不含第 8/26 天
        cumulative = row["cumulative"]
        assert (cumulative["day"], cumulative["users"],
                cumulative["pct"]) == (7, 4, 20.0)
        assert cumulative["mature"] is True
        assert cumulative["heat"] == 0.4
        # 远端第 26 天不会自己变成一行
        assert all(c["day"] <= 30 for c in row["cells"])


    def test_retention_matrix_marks_not_yet_mature_milestones(self, tmp_path):
        """本周的 cohort：周一才装的人第 3 天还没到，不能画成"0 人回访"。"""
        conn = database.connect(tmp_path / "cache.sqlite3")
        today = datetime.strptime(_today_utc8(), "%Y-%m-%d").date()
        monday = today - timedelta(days=today.weekday())     # 本周一
        for index in range(20):
            _seed_daily(conn, monday.isoformat(), f"u{index}",
                        first_day=monday.isoformat())

        rows = metrics_user.retention_matrix(conn, min_cohort_n=20)
        assert len(rows) == 1
        cells = {c["day"]: c for c in rows[0]["cells"]}
        elapsed = (today - monday).days                        # 本周已经过去几天
        for day_n, cell in cells.items():
            if day_n <= elapsed:
                assert cell["mature"] is True
            else:
                assert cell["mature"] is False, f"D{day_n} 不该算到期"
        # D30 在本周必然还没到期
        assert cells[30]["mature"] is False


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
