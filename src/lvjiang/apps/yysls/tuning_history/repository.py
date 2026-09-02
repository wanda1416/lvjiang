"""版本化 SQLite 调律历史仓库。"""
from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lvjiang import constants

from .models import (
    RESET_ANOMALIES,
    TuningEquipmentResult,
    TuningRunSummary,
)

_setup_lock = threading.Lock()


def default_db_path() -> Path:
    return constants.CONFIG_DIR / "session" / "tuning_history.db"


class _ClosingConnection(sqlite3.Connection):
    """事务上下文退出时同时关闭连接，兼容 Python 3.13 资源检查。"""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load_json(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _migrate_v1(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE tuning_runs (
            run_id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL DEFAULT '',
            username TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'running',
            stop_reason TEXT NOT NULL DEFAULT '',
            selected_slots_json TEXT NOT NULL DEFAULT '[]',
            rule_snapshot_json TEXT NOT NULL DEFAULT '[]',
            config_snapshot_json TEXT NOT NULL DEFAULT '{}',
            total_equipment INTEGER NOT NULL DEFAULT 0,
            tuned_count INTEGER NOT NULL DEFAULT 0,
            recycled_count INTEGER NOT NULL DEFAULT 0,
            skipped_count INTEGER NOT NULL DEFAULT 0,
            reset_count INTEGER NOT NULL DEFAULT 0,
            total_rounds INTEGER NOT NULL DEFAULT 0,
            markdown_path TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE tuning_equipment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES tuning_runs(run_id) ON DELETE CASCADE,
            sequence_id INTEGER NOT NULL,
            slot_key TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL DEFAULT '',
            equip_type TEXT NOT NULL DEFAULT '',
            level REAL,
            quality TEXT NOT NULL DEFAULT '',
            initial_affixes_json TEXT NOT NULL DEFAULT '[]',
            final_affixes_json TEXT NOT NULL DEFAULT '[]',
            final_rating TEXT NOT NULL DEFAULT '',
            rounds INTEGER NOT NULL DEFAULT 0,
            result TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            reset_outcome TEXT NOT NULL DEFAULT '',
            raw_status TEXT NOT NULL DEFAULT '',
            scanned_at TEXT NOT NULL DEFAULT '',
            tuning_started_at TEXT NOT NULL DEFAULT '',
            finished_at TEXT NOT NULL DEFAULT '',
            round_details_json TEXT NOT NULL DEFAULT '[]',
            tuning_mode TEXT NOT NULL DEFAULT '',
            telemetry_stop_reason TEXT NOT NULL DEFAULT '',
            telemetry_final_rating TEXT NOT NULL DEFAULT '',
            resets INTEGER NOT NULL DEFAULT 0,
            UNIQUE(run_id, sequence_id)
        )
    """)
    conn.execute("""
        CREATE TABLE telemetry_deliveries (
            equipment_id INTEGER PRIMARY KEY
                REFERENCES tuning_equipment(id) ON DELETE CASCADE,
            event_id TEXT NOT NULL UNIQUE,
            schema_name TEXT NOT NULL DEFAULT 'yysls.tuning_session',
            schema_version INTEGER,
            state TEXT NOT NULL DEFAULT 'unreported',
            eligible_at TEXT NOT NULL,
            first_attempt_at TEXT,
            last_attempt_at TEXT,
            reported_at TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute("CREATE INDEX idx_tuning_runs_started ON tuning_runs(started_at DESC)")
    conn.execute("CREATE INDEX idx_tuning_equipment_run_order ON tuning_equipment(run_id, sequence_id)")
    conn.execute("CREATE INDEX idx_telemetry_pending ON telemetry_deliveries(state, eligible_at)")


MIGRATIONS: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (1, "initial tuning history schema", _migrate_v1),
]
CURRENT_VERSION = MIGRATIONS[-1][0]


_ANOMALY_CODES = tuple(sorted(RESET_ANOMALIES))
_ANOMALY_PLACEHOLDERS = ",".join("?" for _ in _ANOMALY_CODES)
#: 异常按装备行实时聚合，而不是落到 tuning_runs 的列上：finish_run 只在正常
#: 收尾时写计数，崩在半路的运行计数恒为 0，而那正是最该看到异常的运行。
_ANOMALY_SUBQUERY = (
    "(SELECT COUNT(*) FROM tuning_equipment a"
    " WHERE a.run_id = r.run_id"
    f" AND a.reset_outcome IN ({_ANOMALY_PLACEHOLDERS})) AS anomaly_count"
)


class TuningHistoryRepository:
    """短连接、WAL、可迁移的调律历史仓库。"""

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path else default_db_path()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path), timeout=5.0, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        with _setup_lock:
            conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)")
            row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
            current = int(row[0] or 0)
            for version, _description, migrate in MIGRATIONS:
                if version <= current:
                    continue
                migrate(conn)
                conn.execute("INSERT INTO schema_version(version) VALUES (?)", (version,))

    def schema_version(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
            return int(row[0] or 0)

    def create_run(self, summary: TuningRunSummary) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO tuning_runs (
                    run_id, started_at, finished_at, username, status,
                    stop_reason, selected_slots_json, rule_snapshot_json,
                    config_snapshot_json, markdown_path, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                summary.run_id, summary.started_at, summary.finished_at,
                summary.username, summary.status, summary.stop_reason,
                _json(summary.selected_slots), _json(summary.rule_snapshot),
                _json(summary.config_snapshot), summary.markdown_path, now, now,
            ))

    def save_equipment(self, run_id: str, item: TuningEquipmentResult,
                       *, event_id: str | None = None) -> int:
        with self._connect() as conn:
            cur = conn.execute("""
                INSERT INTO tuning_equipment (
                    run_id, sequence_id, slot_key, name, equip_type, level,
                    quality, initial_affixes_json, final_affixes_json,
                    final_rating, rounds, result, reason, reset_outcome,
                    raw_status, scanned_at, tuning_started_at, finished_at,
                    round_details_json, tuning_mode,
                    telemetry_stop_reason, telemetry_final_rating, resets
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, sequence_id) DO UPDATE SET
                    slot_key=excluded.slot_key, name=excluded.name,
                    equip_type=excluded.equip_type, level=excluded.level,
                    quality=excluded.quality,
                    initial_affixes_json=excluded.initial_affixes_json,
                    final_affixes_json=excluded.final_affixes_json,
                    final_rating=excluded.final_rating, rounds=excluded.rounds,
                    result=excluded.result, reason=excluded.reason,
                    reset_outcome=excluded.reset_outcome,
                    raw_status=excluded.raw_status,
                    scanned_at=excluded.scanned_at,
                    tuning_started_at=excluded.tuning_started_at,
                    finished_at=excluded.finished_at,
                    round_details_json=excluded.round_details_json,
                    tuning_mode=excluded.tuning_mode,
                    telemetry_stop_reason=excluded.telemetry_stop_reason,
                    telemetry_final_rating=excluded.telemetry_final_rating,
                    resets=excluded.resets
            """, (
                run_id, item.equipment_id, item.slot_key, item.name, item.type,
                item.level, item.quality, _json(item.initial_affixes),
                _json(item.final_affixes), item.final_rating, item.rounds,
                item.result, item.reason, item.reset_outcome, item.raw_status,
                item.scanned_at, item.tuning_started_at, item.finished_at,
                _json(item.round_details), item.tuning_mode,
                item.telemetry_stop_reason, item.telemetry_final_rating,
                item.resets,
            ))
            row = conn.execute(
                "SELECT id FROM tuning_equipment WHERE run_id=? AND sequence_id=?",
                (run_id, item.equipment_id),
            ).fetchone()
            equipment_pk = int(row[0] if row else cur.lastrowid)
            if item.entered_tuning and event_id:
                conn.execute("""
                    INSERT OR IGNORE INTO telemetry_deliveries (
                        equipment_id, event_id, eligible_at
                    ) VALUES (?, ?, ?)
                """, (equipment_pk, event_id, item.tuning_started_at))
            return equipment_pk

    def finish_run(self, summary: TuningRunSummary) -> None:
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        with self._connect() as conn:
            conn.execute("""
                UPDATE tuning_runs SET
                    finished_at=?, status=?, stop_reason=?,
                    total_equipment=?, tuned_count=?, recycled_count=?,
                    skipped_count=?, reset_count=?, total_rounds=?,
                    markdown_path=?, updated_at=?
                WHERE run_id=?
            """, (
                summary.finished_at, summary.status, summary.stop_reason,
                summary.total_equipment, summary.tuned_count,
                summary.recycled_count, summary.skipped_count,
                summary.reset_count, summary.total_rounds,
                summary.markdown_path, now, summary.run_id,
            ))

    def list_runs(self, limit: int = 200) -> list[TuningRunSummary]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT r.*, " + _ANOMALY_SUBQUERY +
                " FROM tuning_runs r ORDER BY r.started_at DESC LIMIT ?",
                (*_ANOMALY_CODES, max(1, int(limit))),
            ).fetchall()
        return [self._run_from_row(row) for row in rows]

    def get_run(self, run_id: str) -> TuningRunSummary | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT r.*, " + _ANOMALY_SUBQUERY +
                " FROM tuning_runs r WHERE r.run_id=?",
                (*_ANOMALY_CODES, run_id),
            ).fetchone()
        return self._run_from_row(row) if row else None

    def get_results(self, run_id: str) -> tuple[TuningEquipmentResult, ...]:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM tuning_equipment
                WHERE run_id=? ORDER BY sequence_id ASC
            """, (run_id,)).fetchall()
        return tuple(self._equipment_from_row(row) for row in rows)

    def delete_run(self, run_id: str) -> bool:
        """删除一次任务及其装备、上报状态；外键负责级联清理。"""
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM tuning_runs WHERE run_id=?", (run_id,))
            return cursor.rowcount > 0

    def aggregate_counts(self) -> dict[str, int]:
        with self._connect() as conn:
            run_count = int(conn.execute(
                "SELECT COUNT(*) FROM tuning_runs").fetchone()[0])
            row = conn.execute(f"""
                SELECT COUNT(*) AS total,
                    COALESCE(SUM(result IN ('tuned','tuned_recycled')), 0) AS tuned,
                    COALESCE(SUM(result IN ('recycled','tuned_recycled')), 0) AS recycled,
                    COALESCE(SUM(result='skipped'), 0) AS skipped,
                    COALESCE(SUM(reset_outcome = 'completed'), 0) AS reset_count,
                    COALESCE(SUM(reset_outcome IN ({_ANOMALY_PLACEHOLDERS})), 0)
                        AS anomaly_count
                FROM tuning_equipment
            """, _ANOMALY_CODES).fetchone()
        return {
            "runs": run_count, "total": int(row["total"]),
            "tuned": int(row["tuned"]), "recycled": int(row["recycled"]),
            "skipped": int(row["skipped"]), "reset": int(row["reset_count"]),
            "anomaly": int(row["anomaly_count"]),
        }

    def expire_unreported(self, *, now: datetime | None = None,
                          days: int = 7) -> int:
        current = now or datetime.now(timezone.utc)
        cutoff = (current - timedelta(days=days)).isoformat(timespec="milliseconds")
        with self._connect() as conn:
            cur = conn.execute("""
                UPDATE telemetry_deliveries SET state='expired'
                WHERE state='unreported' AND eligible_at < ?
            """, (cutoff,))
            return cur.rowcount

    def pending_telemetry(self, *, now: datetime | None = None,
                          days: int = 7, limit: int = 200,
                          retry_after_hours: int = 0) -> list[dict]:
        """取待上报行。

        ``retry_after_hours`` > 0 时跳过距上次尝试不足该时长的行。上一轮投影
        抛异常的行会保持 unreported 等待重试，没有这道退避，同一批坏行每次
        都按 eligible_at 排在最前，占满 LIMIT 造成队头阻塞，后面正常的行永远
        轮不上。
        """
        current = now or datetime.now(timezone.utc)
        cutoff = (current - timedelta(days=days)).isoformat(timespec="milliseconds")
        self.expire_unreported(now=current, days=days)
        retry_before = (
            (current - timedelta(hours=retry_after_hours)).isoformat(
                timespec="milliseconds")
            if retry_after_hours > 0 else None
        )
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT d.*, e.*, r.rule_snapshot_json, r.config_snapshot_json
                FROM telemetry_deliveries d
                JOIN tuning_equipment e ON e.id=d.equipment_id
                JOIN tuning_runs r ON r.run_id=e.run_id
                WHERE d.state='unreported' AND d.eligible_at >= ?
                  AND (? IS NULL OR d.last_attempt_at IS NULL
                       OR d.last_attempt_at < ?)
                ORDER BY d.eligible_at ASC, e.id ASC LIMIT ?
            """, (cutoff, retry_before, retry_before,
                  max(1, int(limit)))).fetchall()
        return [dict(row) for row in rows]

    def mark_attempted(self, equipment_ids: Iterable[int], *,
                       at: str, schema_version: int) -> None:
        ids = tuple(int(value) for value in equipment_ids)
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as conn:
            conn.execute(f"""
                UPDATE telemetry_deliveries SET
                    schema_version=?,
                    first_attempt_at=COALESCE(first_attempt_at, ?),
                    last_attempt_at=?, attempt_count=attempt_count+1
                WHERE equipment_id IN ({placeholders})
            """, (schema_version, at, at, *ids))

    def mark_reported(self, equipment_ids: Iterable[int], *, at: str) -> None:
        self._mark_delivery_state(equipment_ids, "reported", at)

    def mark_rejected(self, equipment_ids: Iterable[int]) -> None:
        self._mark_delivery_state(equipment_ids, "rejected", None)

    def _mark_delivery_state(self, equipment_ids: Iterable[int], state: str,
                             reported_at: str | None) -> None:
        ids = tuple(int(value) for value in equipment_ids)
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as conn:
            conn.execute(f"""
                UPDATE telemetry_deliveries SET state=?, reported_at=?
                WHERE equipment_id IN ({placeholders})
            """, (state, reported_at, *ids))

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> TuningRunSummary:
        return TuningRunSummary(
            run_id=row["run_id"], started_at=row["started_at"],
            finished_at=row["finished_at"], username=row["username"],
            status=row["status"], stop_reason=row["stop_reason"],
            selected_slots=tuple(_load_json(row["selected_slots_json"], [])),
            rule_snapshot=tuple(_load_json(row["rule_snapshot_json"], [])),
            total_equipment=int(row["total_equipment"]),
            tuned_count=int(row["tuned_count"]),
            recycled_count=int(row["recycled_count"]),
            skipped_count=int(row["skipped_count"]),
            reset_count=int(row["reset_count"]),
            total_rounds=int(row["total_rounds"]),
            markdown_path=row["markdown_path"],
            config_snapshot=_load_json(row["config_snapshot_json"], {}),
            anomaly_count=(int(row["anomaly_count"] or 0)
                           if "anomaly_count" in row.keys() else 0),
        )

    @staticmethod
    def _equipment_from_row(row: sqlite3.Row) -> TuningEquipmentResult:
        level = row["level"]
        if isinstance(level, float) and level.is_integer():
            level = int(level)
        return TuningEquipmentResult(
            equipment_id=int(row["sequence_id"]), slot_key=row["slot_key"],
            name=row["name"], type=row["equip_type"], level=level,
            quality=row["quality"],
            initial_affixes=tuple(_load_json(row["initial_affixes_json"], [])),
            final_affixes=tuple(_load_json(row["final_affixes_json"], [])),
            final_rating=row["final_rating"], rounds=int(row["rounds"]),
            result=row["result"], reason=row["reason"],
            reset_outcome=row["reset_outcome"], raw_status=row["raw_status"],
            scanned_at=row["scanned_at"],
            tuning_started_at=row["tuning_started_at"],
            finished_at=row["finished_at"],
            round_details=tuple(_load_json(row["round_details_json"], [])),
            tuning_mode=row["tuning_mode"],
            telemetry_stop_reason=row["telemetry_stop_reason"],
            telemetry_final_rating=row["telemetry_final_rating"],
            resets=int(row["resets"]),
        )
