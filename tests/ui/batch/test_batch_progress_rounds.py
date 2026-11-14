"""批量进度页的轮次导航。

必须成立的三个行为：
1. 进入新一轮时整表复位，上一轮的成功不会挂在新一轮未执行的行上；
2. 上一轮结果仍可回看，条目列本身不掺轮次文本；
3. 只有「跳到最新」才恢复自动跟随，属性单元模式下各单元轮次各自推进。
"""
import inspect
from types import SimpleNamespace

from PyQt6.QtWidgets import QLabel, QPushButton, QTableWidget

from lvjiang.ui.batch.batch_tab import (
    ST_NOT_RUN,
    ST_PENDING,
    ST_SUCCESS,
    BatchTab,
    _BatchProgressState,
)


def _bind(target: SimpleNamespace, *names: str) -> None:
    for name in names:
        attribute = inspect.getattr_static(BatchTab, name)
        method = getattr(BatchTab, name)
        setattr(target, name,
                method if isinstance(attribute, staticmethod)
                else method.__get__(target))


def _make_progress(qtbot):
    table = QTableWidget(0, 3)
    qtbot.addWidget(table)
    label = QLabel()
    qtbot.addWidget(label)
    buttons = []
    for _ in range(3):
        button = QPushButton()
        qtbot.addWidget(button)
        buttons.append(button)
    # 「跳到最新」在真实界面里是 checkable：按下态表示正在跟随。
    buttons[2].setCheckable(True)
    progress = SimpleNamespace(
        _progress_table=table,
        _round_label=label,
        _round_prev_btn=buttons[0],
        _round_next_btn=buttons[1],
        _round_latest_btn=buttons[2],
        _progress_row_index={},
        _progress_row_context={},
        _progress_task_plan={},
        _run_progress={},
        _visible_run_id="",
    )
    _bind(
        progress,
        "_render_progress_rows", "_row_round", "_row_label",
        "_set_status_item", "_set_round_controls_visible", "_display_round",
        "_refresh_round_header", "_show_round", "_on_prev_round",
        "_on_next_round", "_on_latest_round", "_scroll_to_running_row",
        "apply_task_plan", "update_run_progress",
    )
    return progress


def _status(progress, row: int) -> str:
    item = progress._progress_table.item(row, 2)
    return item.text() if item else ""


def test_new_round_resets_table_and_keeps_previous_round(qtbot):
    progress = _make_progress(qtbot)
    state = _BatchProgressState(
        entries=["alice", "bob"],
        scripts=[("s1", "脚本1")],
        plans={},
        rounds_total=2,
    )
    progress._run_progress["run"] = state
    progress._visible_run_id = "run"
    progress._render_progress_rows(state)
    progress._refresh_round_header(state)

    # 第 1 轮两个人都成功
    for index, name in enumerate(("alice", "bob")):
        progress.update_run_progress("run", 1, index, name, "s1", ST_SUCCESS)
    assert [_status(progress, row) for row in (0, 1)] == [ST_SUCCESS, ST_SUCCESS]
    assert progress._round_label.text() == "第 1/2 轮"
    assert progress._round_latest_btn.isChecked()

    # 第 2 轮边界：调度器先补发全量待执行
    for index, name in enumerate(("alice", "bob")):
        progress.update_run_progress("run", 2, index, name, "s1", ST_PENDING)
    assert [_status(progress, row) for row in (0, 1)] == [ST_PENDING, ST_PENDING]
    assert progress._round_label.text() == "第 2/2 轮"
    assert progress._round_latest_btn.isChecked()

    # 上一轮仍然可回看；条目列不掺轮次
    progress._on_prev_round()
    assert [_status(progress, row) for row in (0, 1)] == [ST_SUCCESS, ST_SUCCESS]
    assert progress._round_label.text() == "第 1/2 轮"
    assert not progress._round_latest_btn.isChecked()
    assert [progress._progress_table.item(row, 0).text()
            for row in (0, 1)] == ["alice", "bob"]

    # 「下一轮」到最新轮仍是固定视图，不自动跟随
    progress._on_next_round()
    assert progress._round_label.text() == "第 2/2 轮"
    assert not progress._round_latest_btn.isChecked()

    # 只有「跳到最新」才恢复跟随（按下态）
    progress._on_latest_round()
    assert progress._round_label.text() == "第 2/2 轮"
    assert progress._round_latest_btn.isChecked()


def test_finished_round_marks_unreached_rows_not_run(qtbot):
    progress = _make_progress(qtbot)
    state = _BatchProgressState(
        entries=["alice", "bob"],
        scripts=[("s1", "脚本1")],
        plans={},
        rounds_total=1,
        terminal=True,
    )
    progress._run_progress["run"] = state
    progress._visible_run_id = "run"
    progress._render_progress_rows(state)
    state.statuses[(1, 0, "s1")] = ST_SUCCESS
    progress._render_progress_rows(state)

    assert _status(progress, 0) == ST_SUCCESS
    assert _status(progress, 1) == ST_NOT_RUN


def test_attr_mode_rounds_are_per_unit(qtbot):
    progress = _make_progress(qtbot)
    state = _BatchProgressState(
        entries=["单元甲", "单元乙"],
        scripts=[("s1", "脚本1")],
        plans={},
        rounds_total=3,
        attr_mode=True,
    )
    progress._run_progress["run"] = state
    progress._visible_run_id = "run"
    progress._render_progress_rows(state)
    progress._refresh_round_header(state)

    # 单元甲已完成第 1 轮并进入第 2 轮，单元乙还停在第 1 轮
    progress.update_run_progress("run", 1, 0, "单元甲", "s1", ST_SUCCESS)
    progress.update_run_progress("run", 2, 0, "单元甲", "s1", ST_PENDING)
    progress.update_run_progress("run", 1, 1, "单元乙", "s1", ST_SUCCESS)

    assert [_status(progress, row) for row in (0, 1)] == [ST_PENDING, ST_SUCCESS]
    # 条目列只放单元名，轮次只在底部导航
    assert [progress._progress_table.item(row, 0).text()
            for row in (0, 1)] == ["单元甲", "单元乙"]
    assert progress._round_label.text() == "第 2/3 轮"
    assert progress._round_latest_btn.isChecked()

    # 回看第 1 轮时两个单元都显示各自那一轮的结果
    progress._on_prev_round()
    assert [_status(progress, row) for row in (0, 1)] == [ST_SUCCESS, ST_SUCCESS]
