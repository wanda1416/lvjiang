"""增量同步的游标/幂等 upsert 测试：伪造 D1Client，不打真实网络。"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stats_client import database  # noqa: E402
from stats_client.sync import Syncer  # noqa: E402


def _today_utc8() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")


def _day_sub(day: str, n: int) -> str:
    return (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=n)).strftime("%Y-%m-%d")


def _after(row: dict, last_day: str, last_install: str) -> bool:
    """镜像 Syncer 里 daily/install_day_rolls 用的 (day, install_id) keyset 谓词。"""
    if row["day"] != last_day:
        return row["day"] > last_day
    return row["install_id"] > last_install


class FakeD1Client:
    """内存里模拟远端三张表 + installs 计数，SQL 只识别本测试会发的那几种形状。"""

    def __init__(self):
        self.daily: list[dict] = []
        self.roll_batch: list[dict] = []
        self.install_day_rolls: list[dict] = []
        self.installs_count = 0
        self.calls = 0

    def query(self, sql: str, params=None):
        self.calls += 1
        params = params or []
        sql_norm = " ".join(sql.split())
        if "FROM daily" in sql_norm:
            last_day, last_install, limit = params
            rows = [r for r in self.daily if _after(r, last_day, last_install)]
            rows.sort(key=lambda r: (r["day"], r["install_id"]))
            return rows[:limit]
        if "FROM roll_batch" in sql_norm:
            last_recv, last_batch, limit = params
            rows = [r for r in self.roll_batch
                    if r["received_at"] > last_recv or
                    (r["received_at"] == last_recv and r["batch_id"] > last_batch)]
            rows.sort(key=lambda r: (r["received_at"], r["batch_id"]))
            return rows[:limit]
        if "FROM install_day_rolls" in sql_norm and "day <" in sql_norm:
            window_start, last_day, last_install, limit = params
            rows = [r for r in self.install_day_rolls
                    if r["day"] < window_start and _after(r, last_day, last_install)]
            rows.sort(key=lambda r: (r["day"], r["install_id"]))
            return rows[:limit]
        if "FROM install_day_rolls" in sql_norm:
            (window_start,) = params
            return [r for r in self.install_day_rolls if r["day"] >= window_start]
        if "COUNT(*) AS n FROM installs" in sql_norm:
            return [{"n": self.installs_count}]
        raise AssertionError(f"unexpected SQL: {sql}")


def _mkdaily(day, install_id, first_day=None):
    return {"day": day, "install_id": install_id, "first_day": first_day or day,
            "app_version": "0.7.0", "run_env": "desktop", "os_name": "windows",
            "os_release": "11", "arch": "x64", "ui_lang": "zh-CN", "plugin": "yysls"}


def _mkbatch(batch_id, received_at, day, install_id="i1", n_events=1):
    event = {"schema": "yysls.tuning_session", "rolls": [{}, {}]}
    payload = json.dumps([event] * n_events)
    return {"batch_id": batch_id, "install_id": install_id, "day": day,
            "app_version": "0.7.0", "plugin": "yysls", "n_events": n_events,
            "payload": payload, "received_at": received_at}


def _conn(tmp_path) -> sqlite3.Connection:
    return database.connect(tmp_path / "cache.sqlite3")


class TestDailySync:
    def test_first_sync_pulls_everything_in_retention_window(self, tmp_path):
        conn = _conn(tmp_path)
        client = FakeD1Client()
        today = _today_utc8()
        old_day = _day_sub(today, 5)
        client.daily = [_mkdaily(today, "i1"), _mkdaily(today, "i2"),
                        _mkdaily(old_day, "i1", first_day=_day_sub(today, 10))]
        result = Syncer(conn, client).run()
        assert result.ok
        rows = conn.execute(
            "SELECT * FROM remote_daily ORDER BY day, install_id").fetchall()
        assert len(rows) == 3

    def test_second_sync_refetches_lookback_window_but_stays_idempotent(self, tmp_path):
        """1 天回看窗口的边界是"日"不是"行"：只要窗口触到当天，当天全部行都会
        重新拉一遍（``day > since_day`` 对当天恒真）。这是刻意的防御性冗余，
        不是 bug——这里验证的是"重拉不产生重复行"，不是"只拉增量"。"""
        conn = _conn(tmp_path)
        client = FakeD1Client()
        today = _today_utc8()
        client.daily = [_mkdaily(today, "i1")]
        Syncer(conn, client).run()

        # 当天新增一行，模拟第二次同步
        client.daily.append(_mkdaily(today, "i2"))
        client.calls = 0
        result = Syncer(conn, client).run()
        assert result.ok
        daily_result = next(t for t in result.tables if t.table == "remote_daily")
        # 回看窗口把"今天"整天都重新拉了一遍（i1 + i2），这是预期行为
        assert daily_result.fetched == 2
        rows = conn.execute("SELECT install_id FROM remote_daily WHERE day = ?",
                            (today,)).fetchall()
        # 关键断言：REPLACE 不会把 i1 重拉出重复行
        assert {r["install_id"] for r in rows} == {"i1", "i2"}
        assert len(rows) == 2

    def test_cursor_advances_so_stale_days_drop_out_of_lookback(self, tmp_path):
        """游标只在"最近 1 天"打转：一旦游标前进到更晚的日期，更早的日期就不再
        落在 (cursor - 1 天, ∞) 的重拉窗口里了——这是"不必每次全量重扫 90 天"
        的关键，不是要求每天只拉一次（游标停在原地时，那一天会一直被重拉，
        见上一个测试；这里验证的是游标前进之后旧日期能真正被甩出窗口）。"""
        day1, day2, day3 = "2026-08-01", "2026-08-10", "2026-08-11"
        conn = _conn(tmp_path)
        client = FakeD1Client()
        client.daily = [_mkdaily(day1, "i1")]
        Syncer(conn, client).run()  # 游标 -> day1

        client.daily.append(_mkdaily(day2, "i2"))
        Syncer(conn, client).run()  # 游标 -> day2（day1 因回看窗口被重拉一次，预期内）

        # 第三次同步：day3 出现新数据，day1（早于 day2-1天）不该再被拉
        client.daily.append(_mkdaily(day3, "i3"))
        result = Syncer(conn, client).run()
        daily_result = next(t for t in result.tables if t.table == "remote_daily")
        assert daily_result.fetched == 2  # day2（回看窗口）+ day3（新增），day1 被甩出

    def test_pagination_across_multiple_pages(self, tmp_path, monkeypatch):
        import stats_client.sync as sync_mod
        monkeypatch.setattr(sync_mod, "_PAGE_DAILY", 3)
        conn = _conn(tmp_path)
        client = FakeD1Client()
        today = _today_utc8()
        client.daily = [_mkdaily(today, f"i{i:02d}") for i in range(10)]
        result = Syncer(conn, client).run()
        assert result.ok
        daily_result = next(t for t in result.tables if t.table == "remote_daily")
        assert daily_result.fetched == 10
        assert daily_result.requests >= 4  # ceil(10/3) 页 + 1 页判定结束
        rows = conn.execute("SELECT COUNT(*) AS n FROM remote_daily").fetchone()
        assert rows["n"] == 10


class TestRollBatchSync:
    def test_idempotent_rerun_does_not_duplicate(self, tmp_path):
        conn = _conn(tmp_path)
        client = FakeD1Client()
        client.roll_batch = [_mkbatch("b1", "2026-08-20T00:00:00Z", "2026-08-20"),
                             _mkbatch("b2", "2026-08-20T00:00:01Z", "2026-08-20")]
        Syncer(conn, client).run()
        n1 = conn.execute("SELECT COUNT(*) AS n FROM remote_roll_batch").fetchone()["n"]
        assert n1 == 2

        # 中断重跑（模拟：什么都没变化，直接再跑一次）应该幂等
        result = Syncer(conn, client).run()
        assert result.ok
        n2 = conn.execute("SELECT COUNT(*) AS n FROM remote_roll_batch").fetchone()["n"]
        assert n2 == 2
        rb_result = next(t for t in result.tables if t.table == "remote_roll_batch")
        assert rb_result.fetched == 0  # 游标已经在最后一条之后，不该再拉到旧数据

    def test_late_arriving_batch_still_synced_via_received_at_cursor(self, tmp_path):
        """批次延迟上报：day 更早，但 received_at 更晚——必须能同步到。"""
        conn = _conn(tmp_path)
        client = FakeD1Client()
        client.roll_batch = [_mkbatch("b1", "2026-08-20T00:00:00Z", "2026-08-20")]
        Syncer(conn, client).run()

        # 延迟上报：day=08-10（更早），但 received_at 更晚
        client.roll_batch.append(_mkbatch("b2", "2026-08-20T00:00:01Z", "2026-08-10"))
        result = Syncer(conn, client).run()
        assert result.ok
        rows = conn.execute("SELECT batch_id FROM remote_roll_batch").fetchall()
        assert {r["batch_id"] for r in rows} == {"b1", "b2"}


class TestInstallDayRollsSync:
    def test_recent_window_upserts_growing_counts(self, tmp_path):
        conn = _conn(tmp_path)
        client = FakeD1Client()
        today = _today_utc8()
        client.install_day_rolls = [{"install_id": "i1", "day": today, "n_events": 5}]
        Syncer(conn, client).run()
        row = conn.execute("SELECT n_events FROM remote_install_day_rolls "
                           "WHERE install_id='i1' AND day=?", (today,)).fetchone()
        assert row["n_events"] == 5

        # 当天量继续累加，最近窗口应该整窗口重拉覆盖旧值
        client.install_day_rolls[0]["n_events"] = 12
        Syncer(conn, client).run()
        row = conn.execute("SELECT n_events FROM remote_install_day_rolls "
                           "WHERE install_id='i1' AND day=?", (today,)).fetchone()
        assert row["n_events"] == 12


class TestInstallsCount:
    def test_scalar_refreshed_every_sync(self, tmp_path):
        conn = _conn(tmp_path)
        client = FakeD1Client()
        select_sql = "SELECT value FROM remote_scalar WHERE key='installs_count'"
        client.installs_count = 7
        Syncer(conn, client).run()
        row = conn.execute(select_sql).fetchone()
        assert row["value"] == "7"

        client.installs_count = 9
        Syncer(conn, client).run()
        row = conn.execute(select_sql).fetchone()
        assert row["value"] == "9"


class TestMetricDaily:
    def test_recompute_produces_dau_and_roll_counts(self, tmp_path):
        conn = _conn(tmp_path)
        client = FakeD1Client()
        today = _today_utc8()
        client.daily = [_mkdaily(today, "i1"), _mkdaily(today, "i2")]
        client.roll_batch = [_mkbatch("b1", "2026-08-20T00:00:00Z", today, n_events=3)]
        result = Syncer(conn, client).run()
        assert result.ok
        row = conn.execute(
            "SELECT * FROM metric_daily WHERE day=?", (today,)).fetchone()
        assert row["dau"] == 2
        assert row["roll_sessions"] == 3
        assert row["roll_rounds"] == 6  # 每个 session 2 轮 × 3 个 session


class TestSyncRunsRecorded:
    def test_run_recorded_with_error_when_d1_fails(self, tmp_path):
        from stats_client.cloudflare import D1Error

        class FailingClient:
            def query(self, sql, params=None):
                raise D1Error("boom")

        conn = _conn(tmp_path)
        result = Syncer(conn, FailingClient()).run()
        assert not result.ok
        row = conn.execute(
            "SELECT ok, error FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()
        assert row["ok"] == 0
        assert "boom" in row["error"]
