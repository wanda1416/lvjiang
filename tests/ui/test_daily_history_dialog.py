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
        run.log_path.write_text(
            f"2026-01-01 23:00:00.000 | DEBUG    | 调试 {username}\n"
            f"2026-01-01 23:00:00.001 | INFO     | 开始 {username}\n"
            "2026-01-01 23:00:00.002 | ERROR    | 错误\n"
            "Traceback: 测试堆栈\n  多行异常详情\n", encoding="utf-8")
        with sqlite3.connect(repository.db_path) as conn:
            conn.execute("UPDATE task_runs SET started_at=? WHERE task_run_id=?",
                         (date + "T23:00:00", run.task_run_id))
    report = tmp_path / "report.md"
    report.write_text("# 测试批次\n\n批量结果", encoding="utf-8")
    batch.finish(status="failed", report_path=report)
    with sqlite3.connect(repository.db_path) as conn:
        conn.execute("UPDATE batch_runs SET started_at=?, finished_at=?",
                     ("2026-01-01T22:00:00", "2026-01-02T23:30:00"))
    return repository, batch, result


def test_hidden_checked_choices_still_filter_and_preview_selected_files(qtbot, tmp_path, monkeypatch):
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
    def external_open_forbidden(url):
        raise AssertionError("内嵌预览不得启动外部阅读器")
    monkeypatch.setattr("PyQt6.QtGui.QDesktopServices.openUrl", external_open_forbidden)
    viewer = dialog._task_viewer
    assert "开始 用户甲" in viewer.log.toPlainText()
    assert "调试" not in viewer.log.toPlainText()
    viewer.level.setCurrentIndex(viewer.level.findText("WARNING"))
    assert viewer.log.toPlainText() == (
        "2026-01-01 23:00:00.002 | ERROR    | 错误\n"
        "Traceback: 测试堆栈\n  多行异常详情\n")
    viewer.level.setCurrentIndex(viewer.level.findText("DEBUG"))
    assert "调试 用户甲" in viewer.log.toPlainText()
    dialog._open_task_result()
    assert viewer.tabs.currentWidget() is viewer.content
    assert viewer.content.toPlainText() == result.read_text(encoding="utf-8")
    assert "任务记录 ID" in viewer.details.toPlainText()
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
    assert dialog._batch_viewer.content.toPlainText() == "# 测试批次\n\n批量结果"
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


def test_preview_refresh_missing_files_invalid_json_and_large_log(qtbot, tmp_path, monkeypatch):
    from lvjiang.ui import history_viewer

    viewer = history_viewer.HistoryViewer()
    qtbot.addWidget(viewer)
    result = tmp_path / "broken.json"
    result.write_text("broken", encoding="utf-8")
    log = tmp_path / "large.log"
    log.write_text("旧日志\n" * 100 + "2026-01-01 00:00:00.000 | ERROR    | 最后错误\n堆栈详情\n", encoding="utf-8")
    monkeypatch.setattr(history_viewer, "_PREVIEW_BYTES", 100)
    viewer.set_record(content=result, log=log, details="记录详情")
    assert "显示原文" in viewer.content.toPlainText()
    assert "broken" in viewer.content.toPlainText()
    assert "最后错误" in viewer.log.toPlainText()
    assert "堆栈详情" in viewer.log.toPlainText()
    assert "仅预览末尾" in viewer.log_notice.text()
    log.write_text("2026-01-01 00:00:00.000 | INFO     | 新日志\n", encoding="utf-8")
    viewer.refresh_log()
    assert "新日志" in viewer.log.toPlainText()
    assert "最后错误" not in viewer.log.toPlainText()
    log.unlink()
    viewer.refresh_log()
    assert viewer.log.toPlainText() == ""
    assert "无法读取文件" in viewer.log_notice.text()
    assert result.read_text(encoding="utf-8") == "broken"
    viewer.clear()
    assert viewer.details.toPlainText() == ""
    assert viewer.log.toPlainText() == ""
