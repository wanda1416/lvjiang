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


RETENTION_MILESTONES = (1, 3, 7, 14, 30)
RETENTION_CUMULATIVE_DAY = 7
#: 热力色阶的上限：回访率到这个值就算最深。50% 对周留存已经很好了，再往上
#: 全抹平反而看不出差别。
RETENTION_HEAT_FULL_PCT = 50.0


def retention_matrix(conn: sqlite3.Connection, min_cohort_n: int = 20,
                     milestones: tuple[int, ...] = RETENTION_MILESTONES,
                     cumulative_day: int = RETENTION_CUMULATIVE_DAY) -> list[dict]:
    """周 cohort 留存矩阵：一行 = 一个安装周，列 = 第 N 天回访率。

    口径（对齐 ``retention_cohort.sql`` 的按周分组、按天回访）：

    - **cohort = 安装周**（周一为界），行按周**倒序**，新的一周在最上面；
    - **规模 = 第 0 天人数**，即该周新装并且装当天就活跃的人数，是所有百分比的分母；
      它等价于旧页面的「第 0 天」那一行——那一行不是留存，是分母；
    - **D<n> = 恰好第 n 天回访**：``day - first_day == n`` 的去重用户数 / 规模；
    - **N 日内累计 = 第 1..n 天里任一天回访过** 的去重用户数 / 规模，不含安装当天。
      周 cohort 里周一到周日装的人，"恰好第 7 天"落在不同日历日，所以累计口径比
      逐日更稳，独立给一列；
    - 规模 < ``min_cohort_n`` 的周不返回（样本量小时按天/格分组全是噪声）。

    ``day_n`` 是相对**每个人自己的安装日**，不是相对周起点，所以原始明细里会出现
    "第 26 天"这种远端格子；矩阵只取固定里程碑，远端稀疏格子自然消失。

    每个格子另带 ``mature``：该里程碑对这一整周是否已经到期。周里最后一天（周日）
    装的人第 n 天落在 ``week_end + n``，所以 ``today >= week_end + n`` 才算到期。
    没到期和"到期但 0 人回访"是两件事，页面上不能都画成 ``—``。
    """
    today = datetime.strptime(_today_utc8(), "%Y-%m-%d").date()
    rows = conn.execute(
        "SELECT strftime('%Y-%W', first_day) AS cohort_week, "
        "date(first_day, '-' || ((CAST(strftime('%w', first_day) AS INTEGER) + 6) % 7)"
        " || ' days') AS week_start, "
        "CAST(julianday(day) - julianday(first_day) AS INTEGER) AS day_n, "
        "install_id FROM remote_daily "
        "WHERE first_day >= date('now', '-90 day')").fetchall()

    cohorts: dict[str, dict] = {}
    for row in rows:
        bucket = cohorts.setdefault(
            row["cohort_week"], {"week_start": row["week_start"], "days": {}})
        bucket["days"].setdefault(row["day_n"], set()).add(row["install_id"])

    matrix: list[dict] = []
    for cohort_week in sorted(cohorts, reverse=True):
        bucket = cohorts[cohort_week]
        days: dict[int, set[str]] = bucket["days"]
        size = len(days.get(0, ()))
        if size < min_cohort_n:
            continue
        week_start = bucket["week_start"]
        start = datetime.strptime(week_start, "%Y-%m-%d").date()
        end = start + timedelta(days=6)

        def users_on(day_n: int, _days=days) -> set[str]:
            return _days.get(day_n, set())

        cumulative: set[str] = set()
        for day_n in range(1, cumulative_day + 1):
            cumulative |= users_on(day_n)

        matrix.append({
            "cohort_week": cohort_week,
            "week_start": week_start,
            "week_end": end.isoformat(),
            # 周号直接用分组键（%Y-%W），不另算 ISO 周号：跨年附近会差一周
            "label": f"{cohort_week} 周",
            "range": f"{start.strftime('%m/%d')} -> {end.strftime('%m/%d')}",
            "size": size,
            "cells": [
                {"day": day_n, "users": len(users_on(day_n)),
                 "pct": round(len(users_on(day_n)) * 100 / size, 1),
                 "heat": _heat(len(users_on(day_n)) * 100 / size),
                 "mature": today >= end + timedelta(days=day_n)}
                for day_n in milestones
            ],
            "cumulative": {
                "day": cumulative_day,
                "users": len(cumulative),
                "pct": round(len(cumulative) * 100 / size, 1),
                "heat": _heat(len(cumulative) * 100 / size),
                "mature": today >= end + timedelta(days=cumulative_day),
            },
        })
    return matrix


def _heat(pct: float) -> float:
    """把回访率折成 0..1 的热度，供页面按色阶铺底色。"""
    return round(min(pct / RETENTION_HEAT_FULL_PCT, 1.0), 3)


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
