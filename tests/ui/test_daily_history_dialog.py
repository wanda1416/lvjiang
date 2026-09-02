from lvjiang.core.daily_history import (
    BatchRunSession,
    TaskHistoryRepository,
    TaskRunSession,
)
from lvjiang.ui.daily_history_dialog import DailyHistoryDialog


def test_batch_history_drills_down_to_its_task_runs(qtbot, tmp_path):
    repository = TaskHistoryRepository(tmp_path / "daily_history.db")
    batch = BatchRunSession(
        config_name="测试批次", input_snapshot={"rows": [{"user": "甲"}]},
        repository=repository,
    )
    task = TaskRunSession(
        username="甲", task_id="auto_tuning", task_name="自动调律",
        task_scope="dedicated", params={"slots": ["ring"]},
        source="batch", batch_run_id=batch.batch_run_id,
        repository=repository, log_root=tmp_path / "logs",
    )
    task.finish(status="completed")
    batch.finish(status="completed")

    dialog = DailyHistoryDialog(repository=repository)
    qtbot.addWidget(dialog)

    assert dialog._tabs.tabText(0) == "任务历史"
    assert dialog._tabs.tabText(1) == "批量历史"
    assert dialog._batch_table.rowCount() == 1
    dialog._batch_table.selectRow(0)
    dialog._view_batch_tasks()

    assert dialog._tabs.currentIndex() == 0
    assert dialog._batch_filter == batch.batch_run_id
    assert dialog._task_table.rowCount() == 1
    assert dialog._task_records[0].task_run_id == task.task_run_id
    assert dialog._task_records[0].batch_run_id == batch.batch_run_id
