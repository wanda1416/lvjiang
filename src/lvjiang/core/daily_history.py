"""统一任务历史、批量历史与逐任务日志归档。"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from loguru import logger

from .. import constants

_SCHEMA_LOCK = threading.Lock()


def default_db_path() -> Path:
    return constants.SESSION_PATH.parent / "daily_history.db"


def default_log_root() -> Path:
    # 延续既定目录约定；其中现在同时包含 daily 与 dedicated 任务。
    return constants.PROJECT_ROOT / "logs" / "daily"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _load_json(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


def _safe_component(value: str, fallback: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return value or fallback


def _stored_path(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return str(path.resolve().relative_to(constants.PROJECT_ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def resolve_history_path(value: str) -> Path | None:
    if not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else constants.PROJECT_ROOT / path


@dataclass(frozen=True)
class TaskRunRecord:
    task_run_id: str
    batch_run_id: str
    username: str
    task_id: str
    task_name: str
    task_scope: str
    source: str
    status: str
    started_at: str
    finished_at: str
    duration_ms: int
    params: Any
    result_path: str
    log_path: str
    error_message: str


@dataclass(frozen=True)
class BatchRunRecord:
    batch_run_id: str
    config_name: str
    status: str
    started_at: str
    finished_at: str
    duration_ms: int
    input_snapshot: Any
    report_path: str
    error_message: str
    task_count: int = 0


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class TaskHistoryRepository:
    """短连接 SQLite 仓储，保存单任务与批次两级执行实例。"""

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path else default_db_path()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path), timeout=5.0, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        with _SCHEMA_LOCK:
            conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS batch_runs (
                    batch_run_id TEXT PRIMARY KEY,
                    config_name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'running',
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL DEFAULT '',
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    input_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    report_path TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS task_runs (
                    task_run_id TEXT PRIMARY KEY,
                    batch_run_id TEXT REFERENCES batch_runs(batch_run_id),
                    username TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    task_name TEXT NOT NULL,
                    task_scope TEXT NOT NULL DEFAULT 'daily',
                    source TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'running',
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL DEFAULT '',
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    params_json TEXT NOT NULL DEFAULT '{}',
                    result_path TEXT NOT NULL DEFAULT '',
                    log_path TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_task_runs_started
                    ON task_runs(started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_task_runs_user_started
                    ON task_runs(username, started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_task_runs_task_started
                    ON task_runs(task_id, started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_task_runs_batch
                    ON task_runs(batch_run_id, started_at);
                CREATE INDEX IF NOT EXISTS idx_batch_runs_started
                    ON batch_runs(started_at DESC);
            """)

    def create_task_run(self, record: TaskRunRecord) -> None:
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO task_runs (
                    task_run_id, batch_run_id, username, task_id, task_name,
                    task_scope, source, status, started_at, finished_at,
                    duration_ms, params_json, result_path, log_path,
                    error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record.task_run_id, record.batch_run_id or None,
                record.username, record.task_id, record.task_name,
                record.task_scope, record.source, record.status,
                record.started_at, record.finished_at, record.duration_ms,
                _json(record.params), record.result_path, record.log_path,
                record.error_message,
            ))

    def finish_task_run(
        self, task_run_id: str, *, status: str, finished_at: str,
        duration_ms: int, result_path: Path | None = None,
        error_message: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute("""
                UPDATE task_runs SET status=?, finished_at=?, duration_ms=?,
                    result_path=?, error_message=? WHERE task_run_id=?
            """, (
                status, finished_at, max(0, int(duration_ms)),
                _stored_path(result_path), error_message, task_run_id,
            ))

    def list_task_runs(
        self, *, usernames: list[str] | None = None,
        task_ids: list[str] | None = None, batch_run_id: str | None = None,
        start_date: date | None = None, end_date: date | None = None,
        limit: int = 2000,
    ) -> list[TaskRunRecord]:
        clauses: list[str] = []
        args: list[Any] = []
        if usernames:
            clauses.append("username IN (%s)" % ",".join("?" * len(usernames)))
            args.extend(usernames)
        if task_ids:
            clauses.append("task_id IN (%s)" % ",".join("?" * len(task_ids)))
            args.extend(task_ids)
        if batch_run_id:
            clauses.append("batch_run_id=?")
            args.append(batch_run_id)
        if start_date:
            clauses.append("started_at >= ?")
            args.append(f"{start_date.isoformat()}T00:00:00")
        if end_date:
            clauses.append("started_at < ?")
            args.append(f"{(end_date + timedelta(days=1)).isoformat()}T00:00:00")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        args.append(max(1, int(limit)))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM task_runs" + where
                + " ORDER BY started_at DESC LIMIT ?", args).fetchall()
        return [self._task_from_row(row) for row in rows]

    def create_batch_run(self, record: BatchRunRecord) -> None:
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO batch_runs (
                    batch_run_id, config_name, status, started_at, finished_at,
                    duration_ms, input_snapshot_json, report_path, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record.batch_run_id, record.config_name, record.status,
                record.started_at, record.finished_at, record.duration_ms,
                _json(record.input_snapshot), record.report_path,
                record.error_message,
            ))

    def finish_batch_run(
        self, batch_run_id: str, *, status: str, finished_at: str,
        duration_ms: int, report_path: Path | None = None,
        error_message: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute("""
                UPDATE batch_runs SET status=?, finished_at=?, duration_ms=?,
                    report_path=?, error_message=? WHERE batch_run_id=?
            """, (
                status, finished_at, max(0, int(duration_ms)),
                _stored_path(report_path), error_message, batch_run_id,
            ))

    def list_batch_runs(
        self, *, start_date: date | None = None,
        end_date: date | None = None, limit: int = 1000,
    ) -> list[BatchRunRecord]:
        clauses: list[str] = []
        args: list[Any] = []
        if start_date:
            clauses.append("b.started_at >= ?")
            args.append(f"{start_date.isoformat()}T00:00:00")
        if end_date:
            clauses.append("b.started_at < ?")
            args.append(f"{(end_date + timedelta(days=1)).isoformat()}T00:00:00")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        args.append(max(1, int(limit)))
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT b.*, COUNT(t.task_run_id) AS task_count
                FROM batch_runs b LEFT JOIN task_runs t
                    ON t.batch_run_id=b.batch_run_id
            """ + where + " GROUP BY b.batch_run_id"
                " ORDER BY b.started_at DESC LIMIT ?", args).fetchall()
        return [self._batch_from_row(row) for row in rows]

    def filter_options(self) -> tuple[list[str], list[tuple[str, str]]]:
        with self._connect() as conn:
            users = [str(row[0]) for row in conn.execute(
                "SELECT DISTINCT username FROM task_runs ORDER BY username")]
            tasks = [(str(row[0]), str(row[1])) for row in conn.execute("""
                SELECT task_id, MAX(task_name) FROM task_runs
                GROUP BY task_id ORDER BY MAX(task_name), task_id
            """)]
        return users, tasks

    def date_bounds(self) -> tuple[date, date]:
        today = datetime.now().astimezone().date()
        with self._connect() as conn:
            row = conn.execute("""
                SELECT MIN(started_at), MAX(started_at) FROM (
                    SELECT started_at FROM task_runs
                    UNION ALL SELECT started_at FROM batch_runs
                )
            """).fetchone()
        try:
            earliest = date.fromisoformat(str(row[0])[:10]) if row and row[0] else today
            latest = date.fromisoformat(str(row[1])[:10]) if row and row[1] else today
        except ValueError:
            return today, today
        return earliest, latest

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> TaskRunRecord:
        return TaskRunRecord(
            task_run_id=str(row["task_run_id"]),
            batch_run_id=str(row["batch_run_id"] or ""),
            username=str(row["username"]), task_id=str(row["task_id"]),
            task_name=str(row["task_name"]), task_scope=str(row["task_scope"]),
            source=str(row["source"]), status=str(row["status"]),
            started_at=str(row["started_at"]), finished_at=str(row["finished_at"]),
            duration_ms=int(row["duration_ms"]),
            params=_load_json(row["params_json"], {}),
            result_path=str(row["result_path"]), log_path=str(row["log_path"]),
            error_message=str(row["error_message"]),
        )

    @staticmethod
    def _batch_from_row(row: sqlite3.Row) -> BatchRunRecord:
        return BatchRunRecord(
            batch_run_id=str(row["batch_run_id"]),
            config_name=str(row["config_name"]), status=str(row["status"]),
            started_at=str(row["started_at"]), finished_at=str(row["finished_at"]),
            duration_ms=int(row["duration_ms"]),
            input_snapshot=_load_json(row["input_snapshot_json"], {}),
            report_path=str(row["report_path"]),
            error_message=str(row["error_message"]),
            task_count=int(row["task_count"]),
        )


class TaskRunSession:
    """一次单任务执行：生成 task_run_id 并归档该线程的日志。"""

    def __init__(
        self, *, username: str, task_id: str, task_name: str,
        task_scope: str, params: Any, source: str, batch_run_id: str = "",
        repository: TaskHistoryRepository | None = None,
        log_root: Path | None = None,
    ):
        self.repository = repository or TaskHistoryRepository()
        self.task_run_id = uuid.uuid4().hex
        self.started_at = _now()
        self._started_monotonic = time.monotonic()
        user_dir = (log_root or default_log_root()) / _safe_component(
            username, "default")
        user_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.log_path = user_dir / (
            f"{_safe_component(task_id, 'task')}_{stamp}_"
            f"{self.task_run_id[:8]}.log")
        self.repository.create_task_run(TaskRunRecord(
            task_run_id=self.task_run_id, batch_run_id=batch_run_id,
            username=username, task_id=task_id, task_name=task_name,
            task_scope=task_scope, source=source, status="running",
            started_at=self.started_at, finished_at="", duration_ms=0,
            params=params, result_path="", log_path=_stored_path(self.log_path),
            error_message="",
        ))

    @contextmanager
    def capture_logs(self) -> Iterator[None]:
        thread_id = threading.get_ident()
        try:
            sink_id = logger.add(
                str(self.log_path), level="DEBUG", encoding="utf-8",
                format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {message}",
                filter=lambda record: record["thread"].id == thread_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"任务独立日志创建失败，继续执行任务: {exc}")
            yield
            return
        try:
            yield
        finally:
            try:
                logger.remove(sink_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"任务独立日志关闭失败: {exc}")

    def finish(self, *, status: str, result_path: Path | None = None,
               error_message: str = "") -> None:
        finished = datetime.now().astimezone()
        self.repository.finish_task_run(
            self.task_run_id, status=status,
            finished_at=finished.isoformat(timespec="milliseconds"),
            duration_ms=int((time.monotonic() - self._started_monotonic) * 1000),
            result_path=result_path, error_message=error_message,
        )


class BatchRunSession:
    """一次批量执行：生成 batch_run_id 并保存批量输入快照。"""

    def __init__(self, *, config_name: str, input_snapshot: Any,
                 repository: TaskHistoryRepository | None = None):
        self.repository = repository or TaskHistoryRepository()
        self.batch_run_id = uuid.uuid4().hex
        self._started_monotonic = time.monotonic()
        self.repository.create_batch_run(BatchRunRecord(
            batch_run_id=self.batch_run_id, config_name=config_name,
            status="running", started_at=_now(), finished_at="",
            duration_ms=0, input_snapshot=input_snapshot, report_path="",
            error_message="",
        ))

    def finish(self, *, status: str, report_path: Path | None = None,
               error_message: str = "") -> None:
        self.repository.finish_batch_run(
            self.batch_run_id, status=status, finished_at=_now(),
            duration_ms=int((time.monotonic() - self._started_monotonic) * 1000),
            report_path=report_path, error_message=error_message,
        )


def try_create_task_run(**kwargs) -> TaskRunSession | None:
    try:
        return TaskRunSession(**kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"任务历史创建失败，继续执行任务: {exc}")
        return None


def try_create_batch_run(**kwargs) -> BatchRunSession | None:
    try:
        return BatchRunSession(**kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"批量历史创建失败，继续执行批量任务: {exc}")
        return None
