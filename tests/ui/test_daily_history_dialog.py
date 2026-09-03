from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QAbstractItemView, QListView

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


def test_task_filters_use_fixed_multi_column_layout(qtbot, tmp_path):
    repository = TaskHistoryRepository(tmp_path / "daily_history.db")
    dialog = DailyHistoryDialog(repository=repository)
    qtbot.addWidget(dialog)

    assert dialog._users.column_count == 3
    assert dialog._tasks.column_count == 2
    for widget in (dialog._users, dialog._tasks):
        assert widget.flow() == QListView.Flow.LeftToRight
        assert widget.isWrapping()
        assert (widget.selectionMode()
                == QAbstractItemView.SelectionMode.MultiSelection)

    dialog._users.addItems([f"用户{i}" for i in range(6)])
    dialog._tasks.addItems([f"任务{i}" for i in range(4)])
    dialog.show()
    qtbot.wait(10)

    user_rects = [dialog._users.visualItemRect(dialog._users.item(i))
                  for i in range(4)]
    assert user_rects[0].y() == user_rects[1].y() == user_rects[2].y()
    assert user_rects[0].x() < user_rects[1].x() < user_rects[2].x()
    assert user_rects[3].x() == user_rects[0].x()
    assert user_rects[3].y() > user_rects[0].y()

    task_rects = [dialog._tasks.visualItemRect(dialog._tasks.item(i))
                  for i in range(3)]
    assert task_rects[0].y() == task_rects[1].y()
    assert task_rects[0].x() < task_rects[1].x()
    assert task_rects[2].x() == task_rects[0].x()
    assert task_rects[2].y() > task_rects[0].y()

    for rect in user_rects[:2]:
        qtbot.mouseClick(dialog._users.viewport(),
                         Qt.MouseButton.LeftButton, pos=rect.center())
    assert len(dialog._users.selectedItems()) == 2
