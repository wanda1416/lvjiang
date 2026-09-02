"""调律管理历史列表与现有结果总览复用。"""
from __future__ import annotations

from dataclasses import replace

from PyQt6.QtWidgets import QMessageBox

from lvjiang.apps.yysls.tuning_history.models import (
    RESET_COUNT_UNREADABLE,
    TuningEquipmentResult,
    TuningRunSummary,
)
from lvjiang.apps.yysls.tuning_history.repository import TuningHistoryRepository
from lvjiang.apps.yysls.ui.tuning.history_widget import TuningHistoryWidget
from lvjiang.apps.yysls.ui.tuning.management_widget import TuningManagementWidget


def _seed(repo):
    run = TuningRunSummary(
        run_id="run-1", started_at="2026-09-01T01:00:00+00:00",
        finished_at="", username="小明", status="running", stop_reason="",
        selected_slots=("chest",), rule_snapshot=({"key": "huiyi"},),
    )
    repo.create_run(run)
    item = TuningEquipmentResult(
        equipment_id=1, slot_key="chest", name="流星甲", type="胸甲",
        level=110, quality="gold", final_affixes=({"name": "会心率"},),
        final_rating="excellent", rounds=2, result="tuned", reason="达到目标",
    )
    repo.save_equipment("run-1", item)
    repo.finish_run(replace(
        run, finished_at="2026-09-01T01:02:00+00:00", status="completed",
        total_equipment=1, tuned_count=1, total_rounds=2))


def test_history_lists_runs_and_opens_existing_overview(qtbot, tmp_path):
    repo = TuningHistoryRepository(tmp_path / "history.db")
    _seed(repo)
    widget = TuningHistoryWidget(repo)
    qtbot.addWidget(widget)

    assert widget._table.rowCount() == 1
    assert "处理 1 件" in widget._stats.text()
    assert widget._selected_run_id() == "run-1"
    assert widget._open_button.isEnabled()
    assert widget._refresh_button.text() == "刷新记录"
    assert widget._open_button.text() == "查看详情"
    assert "palette(highlight)" in widget._open_button.styleSheet()
    assert "palette(button)" in widget._refresh_button.styleSheet()
    assert "#c62828" in widget._delete_button.styleSheet()
    widget._open_selected()

    assert widget._detail_dialog is not None
    assert [item.name for item in widget._detail_store.results] == ["流星甲"]
    assert "2026-09-01 01:00:00" in widget._detail_dialog.windowTitle()


def test_empty_history_uses_single_centered_content_state(qtbot, tmp_path):
    repo = TuningHistoryRepository(tmp_path / "history.db")
    widget = TuningHistoryWidget(repo)
    qtbot.addWidget(widget)

    assert widget._content.currentWidget() is widget._empty
    assert widget._stats.isHidden()
    assert widget._table.rowCount() == 0
    assert not widget._open_button.isEnabled()
    assert not widget._delete_button.isEnabled()


def test_history_refresh_preserves_selected_run(qtbot, tmp_path):
    repo = TuningHistoryRepository(tmp_path / "history.db")
    _seed(repo)
    widget = TuningHistoryWidget(repo)
    qtbot.addWidget(widget)
    repo.create_run(TuningRunSummary(
        run_id="run-2", started_at="2026-09-01T02:00:00+00:00",
        finished_at="", username="小明", status="running", stop_reason="",
    ))

    widget.refresh()

    assert widget._selected_run_id() == "run-1"
    assert widget._table.currentRow() == 1


def test_history_can_delete_selected_run_after_confirmation(
    qtbot, tmp_path, monkeypatch,
):
    repo = TuningHistoryRepository(tmp_path / "history.db")
    _seed(repo)
    widget = TuningHistoryWidget(repo)
    qtbot.addWidget(widget)
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    widget._delete_button.click()

    assert repo.get_run("run-1") is None
    assert widget._table.rowCount() == 0
    assert widget._content.currentWidget() is widget._empty


def test_management_forwards_legacy_progress_control_methods(
    qtbot, tmp_path, monkeypatch,
):
    from lvjiang import constants

    monkeypatch.setattr(constants, "CONFIG_DIR", tmp_path / "config")
    widget = TuningManagementWidget()
    qtbot.addWidget(widget)
    assert widget._tabs.tabText(0) == "当前任务"
    calls: list[tuple[str, bool | None]] = []
    widget.progress_widget.set_paused = lambda paused: calls.append(
        ("paused", paused))
    widget.progress_widget.mark_done = lambda: calls.append(("done", None))

    widget.set_paused(True)
    widget.mark_done()

    assert calls == [("paused", True), ("done", None)]


def _seed_with_anomaly(repo):
    run = TuningRunSummary(
        run_id="run-bad", started_at="2026-09-01T02:00:00+00:00",
        finished_at="", username="小明", status="running", stop_reason="",
        selected_slots=("leg",),
    )
    repo.create_run(run)
    for seq, outcome in ((1, RESET_COUNT_UNREADABLE), (2, "")):
        repo.save_equipment("run-bad", TuningEquipmentResult(
            equipment_id=seq, slot_key="leg", name=f"胫甲{seq}", type="胫甲",
            level=110, quality="gold", final_affixes=(), final_rating="",
            rounds=0, result="reset" if outcome else "skipped", reason="",
            reset_outcome=outcome,
        ))
    return run


def test_run_list_surfaces_anomaly_count(qtbot, tmp_path):
    """识别异常要在历史列表这一层就看得见，不必逐条打开详情去找卡片。"""
    repo = TuningHistoryRepository(tmp_path / "h.db")
    _seed_with_anomaly(repo)

    widget = TuningHistoryWidget(repository=repo)
    qtbot.addWidget(widget)

    assert widget._table.horizontalHeaderItem(5).text() == "异常"
    assert widget._table.item(0, 5).text() == "1"
    assert widget._table.item(0, 5).foreground().color().name() == "#d32f2f"
    assert "1 件装备识别异常" in widget._table.item(0, 5).toolTip()
    assert "异常 1" in widget._stats.text()


def test_run_without_anomaly_leaves_the_column_blank(qtbot, tmp_path):
    """没有异常时该列留空，不要用 0 把表填满噪声。"""
    repo = TuningHistoryRepository(tmp_path / "h.db")
    _seed(repo)

    widget = TuningHistoryWidget(repository=repo)
    qtbot.addWidget(widget)

    assert widget._table.item(0, 5).text() == ""
    assert "异常 0" in widget._stats.text()


def test_anomaly_count_survives_an_unfinished_run(qtbot, tmp_path):
    """崩在半路的运行 finish_run 从没跑过，落库计数全是 0——异常仍要显示。

    这正是异常按装备行实时聚合、而不是落到 tuning_runs 列上的原因。
    """
    repo = TuningHistoryRepository(tmp_path / "h.db")
    _seed_with_anomaly(repo)   # 不调 finish_run

    runs = repo.list_runs()
    assert runs[0].status == "running"
    assert runs[0].total_equipment == 0      # 落库计数确实是空的
    assert runs[0].anomaly_count == 1        # 异常照样算得出来
