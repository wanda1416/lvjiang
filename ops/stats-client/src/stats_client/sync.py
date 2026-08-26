"""增量同步编排：从远端 D1 拉数据、落本地 SQLite、推进游标。

三张远端表三种节奏（已用 ``ops/stats-worker/src/index.js`` 的写入逻辑核对，
见 ``ops/stats-client/README.md`` 的引用）：

- ``daily``：``(day, install_id)`` 写入后不再变（``ON CONFLICT DO NOTHING``），
  keyset 游标即可，只留 1 天回看窗口做防御性冗余。
- ``roll_batch``：写入后不再变，但存在延迟上报，不能按 day 增量，必须用
  ``(received_at, batch_id)`` 复合 keyset 游标。
- ``install_day_rolls``：当天 ``n_events`` 会持续累加，最近 3 天整窗口
  UPSERT，更早日期只追加（append 部分同样用 keyset 游标）。

每一页数据落库和游标前进在同一个 sqlite 事务里提交，崩溃/中断后重跑不会
出现"游标已前进但数据没写完"。
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .cloudflare import D1Client, D1Error

_PAGE_DAILY = 2000
_PAGE_ROLL = 500
_PAGE_IDR = 2000

RETENTION_DAYS = 90          # 远端保留窗口，见 ops/stats-worker/schema.sql 清理钩子
DAILY_LOOKBACK_DAYS = 1      # daily 的防御性重叠窗口
IDR_UPSERT_WINDOW_DAYS = 3   # install_day_rolls 当天量会持续累加，近 3 天整窗口重拉


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today_utc8() -> str:
    # 远端 day 按 UTC+8 固定偏移划分（见 schema.sql），本地水位线用同一口径
    # 才能正确对齐，否则"最近 N 天"窗口会因时区差多算/少算一天。
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")


def _day_sub(day: str, n: int) -> str:
    d = datetime.strptime(day, "%Y-%m-%d") - timedelta(days=n)
    return d.strftime("%Y-%m-%d")


@dataclass
class TableSyncResult:
    table: str
    fetched: int = 0
    inserted_or_updated: int = 0
    requests: int = 0
    error: str | None = None

    def as_dict(self) -> dict:
        return {"table": self.table, "fetched": self.fetched,
                "inserted_or_updated": self.inserted_or_updated,
                "requests": self.requests, "error": self.error}


@dataclass
class SyncResult:
    ok: bool
    started_at: str
    finished_at: str
    duration_ms: int
    tables: list[TableSyncResult] = field(default_factory=list)


class Syncer:
    def __init__(self, conn: sqlite3.Connection, client: D1Client):
        self.conn = conn
        self.client = client

    # ── 游标存取 ──────────────────────────────────────────

    def _get_cursor(self, table: str) -> tuple[str | None, str | None]:
        row = self.conn.execute(
            "SELECT cursor_a, cursor_b FROM sync_cursor WHERE table_name = ?",
            (table,)).fetchone()
        return (row["cursor_a"], row["cursor_b"]) if row else (None, None)

    def _set_cursor(self, table: str, a: str | None, b: str | None) -> None:
        self.conn.execute(
            "INSERT INTO sync_cursor (table_name, cursor_a, cursor_b) VALUES (?, ?, ?) "
            "ON CONFLICT(table_name) DO UPDATE SET cursor_a = excluded.cursor_a, "
            "cursor_b = excluded.cursor_b",
            (table, a, b))

    # ── 主流程 ────────────────────────────────────────────

    def run(self) -> SyncResult:
        started = _now_iso()
        t0 = time.monotonic()
        results = []
        for fn in (self._sync_daily, self._sync_roll_batch,
                   self._sync_install_day_rolls, self._sync_installs_count):
            try:
                results.append(fn())
            except D1Error as e:
                results.append(TableSyncResult(table=fn.__name__, error=str(e)))
        self._recompute_metric_daily()
        finished = _now_iso()
        duration_ms = int((time.monotonic() - t0) * 1000)
        ok = all(r.error is None for r in results)
        self._record_run(started, finished, duration_ms, ok, results)
        return SyncResult(ok=ok, started_at=started, finished_at=finished,
                           duration_ms=duration_ms, tables=results)

    def _record_run(self, started, finished, duration_ms, ok, results) -> None:
        self.conn.execute(
            "INSERT INTO sync_runs (started_at, finished_at, ok, detail_json, "
            "duration_ms, error) VALUES (?, ?, ?, ?, ?, ?)",
            (started, finished, int(ok),
             json.dumps([r.as_dict() for r in results], ensure_ascii=False),
             duration_ms,
             "; ".join(r.error for r in results if r.error) or None))

    # ── remote_daily ──────────────────────────────────────

    def _sync_daily(self) -> TableSyncResult:
        res = TableSyncResult(table="remote_daily")
        cursor_a, _ = self._get_cursor("remote_daily")
        since_day = (_day_sub(cursor_a, DAILY_LOOKBACK_DAYS) if cursor_a
                     else _day_sub(_today_utc8(), RETENTION_DAYS))
        last_day, last_install = since_day, ""
        max_day_seen = cursor_a or since_day
        while True:
            rows = self.client.query(
                "SELECT day, install_id, first_day, app_version, run_env, os_name, "
                "os_release, arch, ui_lang, plugin FROM daily "
                "WHERE day > ?1 OR (day = ?1 AND install_id > ?2) "
                "ORDER BY day, install_id LIMIT ?3",
                [last_day, last_install, _PAGE_DAILY])
            res.requests += 1
            if not rows:
                break
            self.conn.execute("BEGIN")
            for r in rows:
                self.conn.execute(
                    "INSERT OR REPLACE INTO remote_daily (day, install_id, first_day, "
                    "app_version, run_env, os_name, os_release, arch, ui_lang, plugin) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (r["day"], r["install_id"], r["first_day"], r["app_version"],
                     r["run_env"], r["os_name"], r["os_release"], r["arch"],
                     r["ui_lang"], r["plugin"]))
            last_day, last_install = rows[-1]["day"], rows[-1]["install_id"]
            max_day_seen = max(max_day_seen, last_day)
            self._set_cursor("remote_daily", max_day_seen, None)
            self.conn.execute("COMMIT")
            res.fetched += len(rows)
            res.inserted_or_updated += len(rows)
            if len(rows) < _PAGE_DAILY:
                break
        return res

    # ── remote_roll_batch ─────────────────────────────────

    def _sync_roll_batch(self) -> TableSyncResult:
        res = TableSyncResult(table="remote_roll_batch")
        cursor_a, cursor_b = self._get_cursor("remote_roll_batch")
        if cursor_a is None:
            cursor_a, cursor_b = "", ""
        while True:
            rows = self.client.query(
                "SELECT batch_id, install_id, day, app_version, plugin, n_events, "
                "payload, received_at FROM roll_batch "
                "WHERE received_at > ?1 OR (received_at = ?1 AND batch_id > ?2) "
                "ORDER BY received_at, batch_id LIMIT ?3",
                [cursor_a, cursor_b, _PAGE_ROLL])
            res.requests += 1
            if not rows:
                break
            self.conn.execute("BEGIN")
            for r in rows:
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO remote_roll_batch (batch_id, install_id, "
                    "day, app_version, plugin, n_events, payload, received_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (r["batch_id"], r["install_id"], r["day"], r["app_version"],
                     r["plugin"], r["n_events"], r["payload"], r["received_at"]))
                res.inserted_or_updated += cur.rowcount
            cursor_a, cursor_b = rows[-1]["received_at"], rows[-1]["batch_id"]
            self._set_cursor("remote_roll_batch", cursor_a, cursor_b)
            self.conn.execute("COMMIT")
            res.fetched += len(rows)
            if len(rows) < _PAGE_ROLL:
                break
        return res

    # ── remote_install_day_rolls ──────────────────────────

    def _sync_install_day_rolls(self) -> TableSyncResult:
        res = TableSyncResult(table="remote_install_day_rolls")
        cursor_a, _ = self._get_cursor("remote_install_day_rolls")
        today = _today_utc8()
        window_start = _day_sub(today, IDR_UPSERT_WINDOW_DAYS - 1)

        # 1) 更早、较稳定的区间：只追加没拉过的部分
        append_since = cursor_a or _day_sub(today, RETENTION_DAYS)
        if append_since < window_start:
            last_day, last_install = append_since, ""
            while True:
                rows = self.client.query(
                    "SELECT install_id, day, n_events FROM install_day_rolls "
                    "WHERE day < ?1 AND (day > ?2 OR (day = ?2 AND install_id > ?3)) "
                    "ORDER BY day, install_id LIMIT ?4",
                    [window_start, last_day, last_install, _PAGE_IDR])
                res.requests += 1
                if not rows:
                    break
                self.conn.execute("BEGIN")
                for r in rows:
                    self.conn.execute(
                        "INSERT OR REPLACE INTO remote_install_day_rolls "
                        "(install_id, day, n_events) VALUES (?, ?, ?)",
                        (r["install_id"], r["day"], r["n_events"]))
                self.conn.execute("COMMIT")
                res.fetched += len(rows)
                res.inserted_or_updated += len(rows)
                last_day, last_install = rows[-1]["day"], rows[-1]["install_id"]
                if len(rows) < _PAGE_IDR:
                    break

        # 2) 最近 IDR_UPSERT_WINDOW_DAYS 天：当天量会持续累加，整窗口重拉 UPSERT
        rows = self.client.query(
            "SELECT install_id, day, n_events FROM install_day_rolls WHERE day >= ?1",
            [window_start])
        res.requests += 1
        if rows:
            self.conn.execute("BEGIN")
            for r in rows:
                self.conn.execute(
                    "INSERT OR REPLACE INTO remote_install_day_rolls "
                    "(install_id, day, n_events) VALUES (?, ?, ?)",
                    (r["install_id"], r["day"], r["n_events"]))
            self.conn.execute("COMMIT")
            res.fetched += len(rows)
            res.inserted_or_updated += len(rows)

        self._set_cursor("remote_install_day_rolls", window_start, None)
        return res

    # ── remote_scalar: installs_count ─────────────────────

    def _sync_installs_count(self) -> TableSyncResult:
        res = TableSyncResult(table="remote_scalar.installs_count")
        rows = self.client.query("SELECT COUNT(*) AS n FROM installs")
        res.requests += 1
        n = rows[0]["n"] if rows else 0
        self.conn.execute("BEGIN")
        self.conn.execute(
            "INSERT OR REPLACE INTO remote_scalar (key, value, updated_at) "
            "VALUES ('installs_count', ?, ?)", (str(n), _now_iso()))
        self.conn.execute("COMMIT")
        res.fetched = res.inserted_or_updated = 1
        return res

    # ── 本地预计算指标 ─────────────────────────────────────

    def _recompute_metric_daily(self) -> None:
        """从本地缓存重算 metric_daily。全表重算而非增量——这份数据在当前
        规模（几百活跃用户）下体积很小，全量重算比维护"哪些天需要重算"的
        增量逻辑更不容易出错。"""
        dau = dict(self.conn.execute(
            "SELECT day, COUNT(*) AS n FROM remote_daily GROUP BY day").fetchall())
        new_installs = dict(self.conn.execute(
            "SELECT first_day AS day, COUNT(DISTINCT install_id) AS n "
            "FROM remote_daily GROUP BY first_day").fetchall())
        roll_rows = self.conn.execute(
            "SELECT b.day AS day, COUNT(*) AS sessions, "
            "COALESCE(SUM(json_array_length("
            "json_extract(s.value, '$.rolls'))), 0) AS rounds "
            "FROM remote_roll_batch b, json_each(b.payload) s "
            "GROUP BY b.day").fetchall()
        rolls = {r["day"]: (r["sessions"], r["rounds"]) for r in roll_rows}
        days = set(dau) | set(new_installs) | set(rolls)
        now = _now_iso()
        self.conn.execute("BEGIN")
        for day in days:
            sessions, rounds = rolls.get(day, (0, 0))
            self.conn.execute(
                "INSERT OR REPLACE INTO metric_daily (day, dau, new_installs, "
                "roll_sessions, roll_rounds, computed_at) VALUES (?, ?, ?, ?, ?, ?)",
                (day, dau.get(day, 0), new_installs.get(day, 0), sessions, rounds, now))
        self.conn.execute("COMMIT")
