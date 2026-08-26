"""用户/活跃度指标：查询对象是本地缓存 sqlite，不是远端。

口径对齐 ``ops/stats-worker/queries/*.sql``（dau/mau/retention_cohort/
version_dist/platform_dist），只是表名换成本地的 ``remote_daily`` 前缀，
及"最近一天"改成参数化的窗口，方便网页按需换look。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone


def _today_utc8() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")


def _day_sub(day: str, n: int) -> str:
    return (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=n)).strftime("%Y-%m-%d")


def overview(conn: sqlite3.Connection) -> dict:
    """总览页顶部卡片需要的一把指标。"""
    today = _today_utc8()
    d7, d30 = _day_sub(today, 6), _day_sub(today, 29)

    def _distinct_installs_since(since: str) -> int:
        row = conn.execute(
            "SELECT COUNT(DISTINCT install_id) AS n FROM remote_daily WHERE day >= ?",
            (since,)).fetchone()
        return row["n"] if row else 0

    dau_row = conn.execute(
        "SELECT dau, roll_sessions, roll_rounds FROM metric_daily WHERE day = ?",
        (today,)).fetchone()
    new_row = conn.execute(
        "SELECT new_installs FROM metric_daily WHERE day = ?", (today,)).fetchone()
    installs_row = conn.execute(
        "SELECT value FROM remote_scalar WHERE key = 'installs_count'").fetchone()
    roll_installs_row = conn.execute(
        "SELECT COUNT(DISTINCT install_id) AS n FROM remote_roll_batch "
        "WHERE day >= ?", (d30,)).fetchone()
    roll_30d = conn.execute(
        "SELECT COALESCE(SUM(roll_sessions), 0) AS sessions, "
        "COALESCE(SUM(roll_rounds), 0) AS rounds FROM metric_daily "
        "WHERE day >= ?", (d30,)).fetchone()
    last_sync = conn.execute(
        "SELECT finished_at, ok FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()

    return {
        "today": today,
        "installs_count_remote_180d": (
            int(installs_row["value"]) if installs_row else None),
        "installs_count_local": conn.execute(
            "SELECT COUNT(DISTINCT install_id) AS n FROM remote_daily").fetchone()["n"],
        "dau_today": dau_row["dau"] if dau_row else 0,
        "wau": _distinct_installs_since(d7),
        "mau": _distinct_installs_since(d30),
        "new_installs_today": new_row["new_installs"] if new_row else 0,
        "roll_installs_30d": roll_installs_row["n"] if roll_installs_row else 0,
        "roll_sessions_30d": roll_30d["sessions"] if roll_30d else 0,
        "roll_rounds_30d": roll_30d["rounds"] if roll_30d else 0,
        "last_sync_at": last_sync["finished_at"] if last_sync else None,
        "last_sync_ok": bool(last_sync["ok"]) if last_sync else None,
    }


def dau_series(conn: sqlite3.Connection, days: int = 90) -> list[dict]:
    """最近 N 天 DAU/新增趋势，供总览页折线图用。对齐 dau.sql 的口径。"""
    since = _day_sub(_today_utc8(), days - 1)
    rows = conn.execute(
        "SELECT day, dau, new_installs, roll_sessions, roll_rounds FROM metric_daily "
        "WHERE day >= ? ORDER BY day", (since,)).fetchall()
    return [dict(r) for r in rows]


def version_dist(conn: sqlite3.Connection) -> list[dict]:
    """最近一天的版本分布。对齐 version_dist.sql。"""
    rows = conn.execute(
        "SELECT app_version, COUNT(*) AS n FROM remote_daily "
        "WHERE day = (SELECT MAX(day) FROM remote_daily) "
        "GROUP BY app_version ORDER BY n DESC").fetchall()
    return [dict(r) for r in rows]


def platform_dist(conn: sqlite3.Connection) -> list[dict]:
    """端类型 / 系统分布。对齐 platform_dist.sql。"""
    rows = conn.execute(
        "SELECT run_env, os_name, COUNT(*) AS n FROM remote_daily "
        "WHERE day = (SELECT MAX(day) FROM remote_daily) "
        "GROUP BY run_env, os_name ORDER BY n DESC").fetchall()
    return [dict(r) for r in rows]


def retention_cohort(conn: sqlite3.Connection, min_cohort_n: int = 20) -> list[dict]:
    """周 cohort 留存。对齐 retention_cohort.sql——人数 < min_cohort_n 的格子
    直接不返回（样本量小时按天/格分组全是噪声，这条规则和 queries/README.md
    的提示一致）。"""
    rows = conn.execute(
        "SELECT strftime('%Y-%W', first_day) AS cohort_week, "
        "CAST(julianday(day) - julianday(first_day) AS INTEGER) AS day_n, "
        "COUNT(DISTINCT install_id) AS users FROM remote_daily "
        "WHERE first_day >= date('now', '-90 day') "
        "GROUP BY cohort_week, day_n ORDER BY cohort_week, day_n").fetchall()
    by_cohort_total: dict[str, int] = {}
    for r in rows:
        if r["day_n"] == 0:
            by_cohort_total[r["cohort_week"]] = r["users"]
    return [dict(r) for r in rows
            if by_cohort_total.get(r["cohort_week"], 0) >= min_cohort_n]


def stop_reason_dist(conn: sqlite3.Connection, days: int = 30) -> list[dict]:
    """结束原因 × 最终评级分布。对齐 stop_reason_dist.sql。"""
    since = _day_sub(_today_utc8(), days - 1)
    rows = conn.execute(
        "SELECT json_extract(s.value, '$.stop_reason') AS stop_reason, "
        "json_extract(s.value, '$.final_rating') AS final_rating, "
        "COUNT(*) AS sessions FROM remote_roll_batch b, json_each(b.payload) s "
        "WHERE b.day >= ? GROUP BY stop_reason, final_rating "
        "ORDER BY sessions DESC", (since,)).fetchall()
    return [dict(r) for r in rows]
