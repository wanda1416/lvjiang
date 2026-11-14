"""批量进度页的轮次维度。

三个必须成立的行为：
1. 进入新一轮时整表复位，上一轮的成功不会挂在新一轮未执行的行上；
2. 上一轮结果仍可回看，不会被新一轮覆盖；
3. 属性单元模式没有全局轮屏障，各单元只看自己的轮次。

这些用例直接绑定 BatchTab 的进度页方法到替身上，避免拉起整页依赖。
"""
import inspect
from types import SimpleNamespace

from PyQt6.QtWidgets import QComboBox, QLabel, QTableWidget

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
    combo = QComboBox()
    qtbot.addWidget(combo)
    progress = SimpleNamespace(
        _progress_table=table,
        _round_label=label,
        _round_combo=combo,
        _progress_row_index={},
        _progress_row_context={},
        _progress_task_plan={},
        _run_progress={},
        _visible_run_id="",
    )
    _bind(
        progress,
        "_render_progress_rows", "_row_round", "_row_label",
        "_set_status_item", "_set_round_controls_visible",
        "_refresh_round_header", "_round_header_text", "_round_option_text",
        "_round_progress", "_completed_unit_rounds", "_round_summary_text",
        "_on_round_view_changed", "_refresh_unit_row_labels",
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
    assert "第 1/2 轮" in progress._round_label.text()

    # 第 2 轮边界：调度器先补发全量待执行
    for index, name in enumerate(("alice", "bob")):
        progress.update_run_progress("run", 2, index, name, "s1", ST_PENDING)
    assert [_status(progress, row) for row in (0, 1)] == [ST_PENDING, ST_PENDING]
    assert "第 2/2 轮" in progress._round_label.text()

    # 上一轮仍然可回看
    history_index = progress._round_combo.findData(1)
    assert history_index > 0
    progress._on_round_view_changed(history_index)
    assert [_status(progress, row) for row in (0, 1)] == [ST_SUCCESS, ST_SUCCESS]
    assert "查看第 1/2 轮" in progress._round_label.text()

    # 切回当前轮恢复实时视图
    progress._on_round_view_changed(0)
    assert [_status(progress, row) for row in (0, 1)] == [ST_PENDING, ST_PENDING]


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
    # 行标题带着各自的轮次，不会把别人的轮次套到自己头上
    assert "第 2/3 轮" in progress._progress_table.item(0, 0).text()
    assert "第 1/3 轮" in progress._progress_table.item(1, 0).text()
