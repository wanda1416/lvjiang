"""统一任务历史与批量历史仓储测试。"""
import sqlite3
from datetime import datetime

from loguru import logger

from lvjiang.core.daily_history import (
    CURRENT_SCHEMA_VERSION,
    BatchRunSession,
    TaskHistoryRepository,
    TaskRunSession,
)


def test_single_task_records_ids_params_result_and_log(tmp_path):
    repository = TaskHistoryRepository(tmp_path / "daily_history.db")
    result_path = tmp_path / "result.json"
    result_path.write_text("{}", encoding="utf-8")
    session = TaskRunSession(
        username="用户甲", task_id="auto_tuning", task_name="自动调律",
        task_scope="dedicated", params={"slots": ["weapon"]},
        source="single", repository=repository,
        target_id="android:device-a", target_kind="adb",
        target_label="设备 A", environment="android", layout="default",
        input_kind="agent",
        log_root=tmp_path / "logs" / "daily",
    )

    with session.capture_logs():
        logger.info("独立任务日志内容")
        logger.bind(task_run_id="another-run").info("不应进入本任务")
    session.finish(status="completed", result_path=result_path)

    records = repository.list_task_runs()
    assert len(records) == 1
    record = records[0]
    assert record.task_run_id == session.task_run_id
    assert record.batch_run_id == ""
    assert record.task_id == "auto_tuning"
    assert record.task_scope == "dedicated"
    assert record.params == {"slots": ["weapon"]}
    assert record.status == "completed"
    assert (record.target_id, record.target_kind, record.target_label) == (
        "android:device-a", "adb", "设备 A")
    assert (record.environment, record.layout, record.input_kind) == (
        "android", "default", "agent")
    assert record.finished_at
    assert record.duration_ms >= 0
    assert record.result_path
    assert "独立任务日志内容" in session.log_path.read_text(encoding="utf-8")
    assert "不应进入本任务" not in session.log_path.read_text(encoding="utf-8")
    assert len(repository.list_task_runs(target_kind="adb")) == 1
    assert repository.list_task_runs(target_kind="windows") == []


def test_batch_id_links_all_task_run_ids_and_supports_drilldown(tmp_path):
    repository = TaskHistoryRepository(tmp_path / "daily_history.db")
    batch = BatchRunSession(
        config_name="双用户日常",
        input_snapshot={"rows": [{"user": "甲"}, {"user": "乙"}]},
        target_id="window", target_kind="windows", target_label="游戏窗口",
        repository=repository,
    )
    first = TaskRunSession(
        username="甲", task_id="daily_checkin", task_name="每日签到",
        task_scope="daily", params={"claim": True}, source="batch",
        batch_run_id=batch.batch_run_id, repository=repository,
        log_root=tmp_path / "logs",
    )
    second = TaskRunSession(
        username="乙", task_id="auto_tuning", task_name="自动调律",
        task_scope="dedicated", params={"dry_run": True}, source="batch",
        batch_run_id=batch.batch_run_id, repository=repository,
        log_root=tmp_path / "logs",
    )
    first.finish(status="completed")
    second.finish(status="failed", error_message="测试失败")
    batch.finish(status="failed")

    tasks = repository.list_task_runs(batch_run_id=batch.batch_run_id)
    assert {item.task_run_id for item in tasks} == {
        first.task_run_id, second.task_run_id,
    }
    assert all(item.batch_run_id == batch.batch_run_id for item in tasks)
    assert {item.task_scope for item in tasks} == {"daily", "dedicated"}
    batches = repository.list_batch_runs()
    assert len(batches) == 1
    assert batches[0].batch_run_id == batch.batch_run_id
    assert batches[0].task_count == 2
    assert (batches[0].target_id, batches[0].target_kind) == (
        "window", "windows")
    assert batches[0].input_snapshot["rows"][1]["user"] == "乙"
    assert len(repository.list_batch_runs(target_kind="windows")) == 1
    assert repository.list_batch_runs(target_kind="adb") == []


def test_user_task_and_date_filters_can_be_combined(tmp_path):
    repository = TaskHistoryRepository(tmp_path / "daily_history.db")
    for username, task_id in (("甲", "a"), ("乙", "a"), ("甲", "b")):
        run = TaskRunSession(
            username=username, task_id=task_id, task_name=task_id.upper(),
            task_scope="daily", params={}, source="single",
            repository=repository, log_root=tmp_path / "logs",
        )
        run.finish(status="completed")

    today = datetime.now().astimezone().date()
    records = repository.list_task_runs(
        usernames=["甲"], task_ids=["a"],
        start_date=today, end_date=today,
    )

    assert [(item.username, item.task_id) for item in records] == [("甲", "a")]


def test_legacy_database_migrates_without_losing_records(tmp_path):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE batch_runs (
                batch_run_id TEXT PRIMARY KEY, config_name TEXT NOT NULL,
                status TEXT NOT NULL, started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL DEFAULT '', duration_ms INTEGER NOT NULL DEFAULT 0,
                input_snapshot_json TEXT NOT NULL DEFAULT '{}', report_path TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE task_runs (
                task_run_id TEXT PRIMARY KEY, batch_run_id TEXT REFERENCES batch_runs(batch_run_id),
                username TEXT NOT NULL, task_id TEXT NOT NULL, task_name TEXT NOT NULL,
                task_scope TEXT NOT NULL, source TEXT NOT NULL, status TEXT NOT NULL,
                started_at TEXT NOT NULL, finished_at TEXT NOT NULL DEFAULT '',
                duration_ms INTEGER NOT NULL DEFAULT 0, params_json TEXT NOT NULL DEFAULT '{}',
                result_path TEXT NOT NULL DEFAULT '', log_path TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT ''
            );
            INSERT INTO task_runs (
                task_run_id, batch_run_id, username, task_id, task_name,
                task_scope, source, status, started_at
            ) VALUES ('legacy-run', NULL, '旧用户', 'old-task', '旧任务',
                      'daily', 'single', 'completed', '2026-01-01T00:00:00');
        """)

    repository = TaskHistoryRepository(db_path)

    assert repository.schema_version() == CURRENT_SCHEMA_VERSION
    record = repository.list_task_runs()[0]
    assert record.task_run_id == "legacy-run"
    assert record.target_id == ""
    assert record.target_kind == ""


def test_status_filters_before_limit_and_keeps_batch_task_count(tmp_path):
    """最新记录成功时仍能找到较早失败，批次计数包含所有状态子任务。"""
    repository = TaskHistoryRepository(tmp_path / "history.db")
    old_batch = BatchRunSession(config_name="旧批次", input_snapshot={}, repository=repository)
    new_batch = BatchRunSession(config_name="新批次", input_snapshot={}, repository=repository)
    runs = []
    for batch, status in ((old_batch, "failed"), (old_batch, "completed"),
                          (new_batch, "completed")):
        run = TaskRunSession(
            username="甲", task_id="a", task_name="任务", task_scope="daily",
            params={}, source="batch", batch_run_id=batch.batch_run_id,
            repository=repository, log_root=tmp_path / "logs")
        run.finish(status=status)
        runs.append(run)
    old_batch.finish(status="failed")
    new_batch.finish(status="completed")
    # 显式日期避免依赖毫秒时钟的先后顺序。
    with sqlite3.connect(repository.db_path) as conn:
        conn.execute("UPDATE task_runs SET started_at='2026-01-01T00:00:00' WHERE task_run_id=?",
                     (runs[0].task_run_id,))
        conn.execute("UPDATE batch_runs SET started_at='2026-01-01T00:00:00' WHERE batch_run_id=?",
                     (old_batch.batch_run_id,))
    assert repository.list_task_runs(status="failed", limit=1)[0].task_run_id == runs[0].task_run_id
    batches = repository.list_batch_runs(status="failed", limit=1)
    assert [(batch.batch_run_id, batch.task_count) for batch in batches] == [(old_batch.batch_run_id, 2)]
