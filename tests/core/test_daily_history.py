"""统一任务历史与批量历史仓储测试。"""
from datetime import datetime

from loguru import logger

from lvjiang.core.daily_history import (
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
        log_root=tmp_path / "logs" / "daily",
    )

    with session.capture_logs():
        logger.info("独立任务日志内容")
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
    assert record.finished_at
    assert record.duration_ms >= 0
    assert record.result_path
    assert "独立任务日志内容" in session.log_path.read_text(encoding="utf-8")


def test_batch_id_links_all_task_run_ids_and_supports_drilldown(tmp_path):
    repository = TaskHistoryRepository(tmp_path / "daily_history.db")
    batch = BatchRunSession(
        config_name="双用户日常",
        input_snapshot={"rows": [{"user": "甲"}, {"user": "乙"}]},
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
    assert batches[0].input_snapshot["rows"][1]["user"] == "乙"


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
