"""历史筛选及批次下钻保护用户可见的查询和文件打开契约。"""
import sqlite3

from PyQt6.QtCore import QDate, Qt

from lvjiang.core.daily_history import (
    BatchRunSession,
    TaskHistoryRepository,
    TaskRunSession,
)
from lvjiang.ui.daily_history_dialog import DailyHistoryDialog


def _history(tmp_path):
    repository = TaskHistoryRepository(tmp_path / "history.db")
    batch = BatchRunSession(config_name="测试批次", input_snapshot={}, repository=repository)
    result = tmp_path / "result.json"
    result.write_text("{}", encoding="utf-8")
    for username, task_id, status, date in (
        ("用户甲", "first", "failed", "2026-01-01"),
        ("用户乙", "second", "completed", "2026-01-02"),
    ):
        run = TaskRunSession(
            username=username, task_id=task_id, task_name="同名任务",
            task_scope="daily", params={"count": 1}, source="batch",
            batch_run_id=batch.batch_run_id, target_kind="adb", target_label="测试设备",
            repository=repository, log_root=tmp_path / "logs")
        run.finish(status=status, result_path=result)
        with sqlite3.connect(repository.db_path) as conn:
            conn.execute("UPDATE task_runs SET started_at=? WHERE task_run_id=?",
                         (date + "T23:00:00", run.task_run_id))
    batch.finish(status="failed")
    with sqlite3.connect(repository.db_path) as conn:
        conn.execute("UPDATE batch_runs SET started_at=?, finished_at=?",
                     ("2026-01-01T22:00:00", "2026-01-02T23:30:00"))
    return repository, batch, result


def test_hidden_checked_choices_still_filter_and_open_selected_result(qtbot, tmp_path, monkeypatch):
    repository, _, result = _history(tmp_path)
    dialog = DailyHistoryDialog(repository=repository)
    qtbot.addWidget(dialog)
    dialog.show()
    user = next(dialog._users.items.item(index) for index in range(dialog._users.items.count())
                if dialog._users.items.item(index).data(Qt.ItemDataRole.UserRole) == "用户甲")
    assert user is not None
    user.setCheckState(Qt.CheckState.Checked)
    dialog._users.search.setText("用户乙")
    assert user.isHidden()
    task = dialog._tasks.items.item(0)
    assert task is not None
    assert "first" in task.text()  # 重名任务仍可区分。
    task.setCheckState(Qt.CheckState.Checked)
    dialog._task_status.setCurrentIndex(dialog._task_status.findData("failed"))
    dialog.refresh_tasks()
    assert [(record.username, record.task_id, record.status) for record in dialog._task_records] == [
        ("用户甲", "first", "failed")]
    assert dialog._open_result_button.isEnabled()
    opened = []
    monkeypatch.setattr("lvjiang.ui.daily_history_dialog.QDesktopServices.openUrl", opened.append)
    dialog._open_task_result()
    assert opened[0].toLocalFile() == str(result)
    assert "任务记录 ID" in dialog._task_detail.toPlainText()
    dialog._users.clear_checks()
    dialog._tasks.clear_checks()
    dialog._task_status.setCurrentIndex(0)
    dialog.refresh_tasks()
    assert len(dialog._task_records) == 2


def test_batch_drilldown_clears_stale_filters_and_covers_midnight(qtbot, tmp_path):
    repository, batch, _ = _history(tmp_path)
    dialog = DailyHistoryDialog(repository=repository)
    qtbot.addWidget(dialog)
    dialog._start_date.setDate(QDate(2026, 1, 1))
    dialog._end_date.setDate(QDate(2026, 1, 1))
    dialog._task_target_kind.setCurrentIndex(dialog._task_target_kind.findData("windows"))
    dialog._task_status.setCurrentIndex(dialog._task_status.findData("completed"))
    dialog._users.items.item(0).setCheckState(Qt.CheckState.Checked)
    dialog._tasks.items.item(0).setCheckState(Qt.CheckState.Checked)
    dialog._users.search.setText("找不到")
    dialog._batch_status.setCurrentIndex(dialog._batch_status.findData("failed"))
    dialog.refresh_batches()
    assert len(dialog._batch_records) == 1
    dialog._view_batch_tasks()
    assert len(dialog._task_records) == 2
    assert {record.batch_run_id for record in dialog._task_records} == {batch.batch_run_id}
    assert dialog._end_date.date() == QDate(2026, 1, 2)
    assert dialog._task_status.currentData() is None
    assert dialog._task_target_kind.currentData() is None
    assert dialog._users.checked_keys() == dialog._tasks.checked_keys() == []
    assert dialog._users.search.text() == ""
    dialog._clear_batch_filter()
    assert dialog._batch_filter == ""
    dialog._batch_status.setCurrentIndex(dialog._batch_status.findData("completed"))
    dialog.refresh_batches()
    assert dialog._batch_records == []
