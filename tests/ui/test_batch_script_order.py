"""Batch script selection order and row-density tests."""

from __future__ import annotations

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QHeaderView

from lvjiang.core.batch_config import BatchConfig, BatchConfigItem
from lvjiang.core.config.models import UserConfig
from lvjiang.ui.batch.batch_runner import ST_PENDING, BatchScript
from lvjiang.ui.batch.batch_tab import BatchTab


class _Host(QObject):
    automation_state_changed = pyqtSignal(str)
    is_running = False

    def __init__(self):
        super().__init__()
        self._user_config = UserConfig()
        self.logs: list[str] = []

    def append_log(self, message: str) -> None:
        self.logs.append(message)


def _scripts() -> list[dict]:
    scripts = [
        {
            "id": script_id,
            "name": f"Script {script_id.upper()}",
            "wf_file": f"{script_id}.wf",
            "class": "",
        }
        for script_id in ("a", "b", "c", "d")
    ]
    scripts.append({
        "id": "standalone",
        "name": "Standalone",
        "wf_file": "standalone/demo.wf",
        "class": "",
        "batchable": False,
    })
    return scripts


def _make_tab(qtbot, monkeypatch, initial_order=()):
    config = BatchConfig(script_ids=list(initial_order))
    saved_orders: list[list[str]] = []
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_tab.load_batch_config", lambda: config
    )

    def _save(saved: BatchConfig) -> None:
        saved_orders.append(list(saved.script_ids))

    monkeypatch.setattr("lvjiang.ui.batch.batch_tab.save_batch_config", _save)
    monkeypatch.setattr(
        "lvjiang.workflows.discovery.list_exposed_scripts", _scripts
    )
    host = _Host()
    tab = BatchTab(host)
    tab._test_host = host
    qtbot.addWidget(tab)
    return tab, saved_orders


def _item_by_id(tab: BatchTab, script_id: str):
    for index in range(tab._script_list.topLevelItemCount()):
        item = tab._script_list.topLevelItem(index)
        if item.data(0, Qt.ItemDataRole.UserRole)["id"] == script_id:
            return item
    raise AssertionError(f"missing script {script_id}")


def test_check_order_is_numbered_and_compacts_after_uncheck(qtbot, monkeypatch):
    tab, saved_orders = _make_tab(qtbot, monkeypatch)

    item_c = _item_by_id(tab, "c")
    item_a = _item_by_id(tab, "a")
    item_d = _item_by_id(tab, "d")
    item_b = _item_by_id(tab, "b")
    item_c.setCheckState(0, Qt.CheckState.Checked)
    item_a.setCheckState(0, Qt.CheckState.Checked)
    item_d.setCheckState(0, Qt.CheckState.Checked)
    item_b.setCheckState(0, Qt.CheckState.Checked)

    assert tab._checked_script_ids() == ["c", "a", "d", "b"]
    assert (
        item_c.text(1), item_a.text(1), item_d.text(1), item_b.text(1)
    ) == ("1", "2", "3", "4")

    item_d.setCheckState(0, Qt.CheckState.Unchecked)

    assert tab._checked_script_ids() == ["c", "a", "b"]
    assert (
        item_c.text(1), item_a.text(1), item_d.text(1), item_b.text(1)
    ) == ("1", "2", "", "3")
    assert saved_orders[-1] == ["c", "a", "b"]


def test_saved_order_controls_execution_and_can_move(qtbot, monkeypatch):
    tab, saved_orders = _make_tab(qtbot, monkeypatch, ("b", "d", "a"))
    item_d = _item_by_id(tab, "d")

    assert [script.id for script in tab._checked_scripts()] == ["b", "d", "a"]
    assert item_d.text(1) == "2"

    tab._script_list.setCurrentItem(item_d)
    tab._move_selected_script(-1)

    assert tab._checked_script_ids() == ["d", "b", "a"]
    assert item_d.text(1) == "1"
    assert saved_orders[-1] == ["d", "b", "a"]


def _row_ids(tab: BatchTab) -> list[str]:
    return [
        tab._script_list.topLevelItem(index).data(
            0, Qt.ItemDataRole.UserRole
        )["id"]
        for index in range(tab._script_list.topLevelItemCount())
    ]


def test_visible_row_order_is_the_execution_order(qtbot, monkeypatch):
    """可见行顺序必须等于执行顺序，否则「上移/下移」看起来毫无反应。"""
    tab, _ = _make_tab(qtbot, monkeypatch, ("b", "d", "a"))

    # 已勾选的按执行顺序排在最前，未勾选的（c）落到后面。
    assert _row_ids(tab) == ["b", "d", "a", "c"]
    assert _row_ids(tab)[:3] == tab._checked_script_ids()

    item_d = _item_by_id(tab, "d")
    tab._script_list.setCurrentItem(item_d)
    tab._move_selected_script(-1)

    assert tab._checked_script_ids() == ["d", "b", "a"]
    assert _row_ids(tab) == ["d", "b", "a", "c"]
    # 选中态要跟着行走，否则连点两次「上移」第二次会静默失败。
    assert tab._script_list.currentItem() is item_d


def test_repeated_move_keeps_walking_the_script_up(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch, ("a", "b", "c", "d"))
    tab._script_list.setCurrentItem(_item_by_id(tab, "d"))

    tab._move_selected_script(-1)
    tab._move_selected_script(-1)
    tab._move_selected_script(-1)

    assert tab._checked_script_ids() == ["d", "a", "b", "c"]
    assert _row_ids(tab) == ["d", "a", "b", "c"]


def test_checking_a_script_appends_it_to_the_visible_order(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch, ("b",))

    _item_by_id(tab, "d").setCheckState(0, Qt.CheckState.Checked)

    assert tab._checked_script_ids() == ["b", "d"]
    assert _row_ids(tab)[:2] == ["b", "d"]

    _item_by_id(tab, "b").setCheckState(0, Qt.CheckState.Unchecked)

    assert tab._checked_script_ids() == ["d"]
    assert _row_ids(tab)[0] == "d"


def test_refresh_scripts_picks_up_exposure_changes(qtbot, monkeypatch):
    """「脚本配置」改过暴露层后，批量页不能还拿着启动时的旧快照。"""
    tab, _ = _make_tab(qtbot, monkeypatch, ("b", "a"))
    assert _row_ids(tab) == ["b", "a", "c", "d"]

    # 取消暴露 a、改掉 b 的显示名，模拟「脚本配置」保存后的发现结果。
    def _changed() -> list[dict]:
        return [
            {"id": "b", "name": "改过的名字", "wf_file": "b.wf", "class": ""},
            {"id": "c", "name": "Script C", "wf_file": "c.wf", "class": ""},
            {"id": "d", "name": "Script D", "wf_file": "d.wf", "class": ""},
        ]

    monkeypatch.setattr(
        "lvjiang.workflows.discovery.list_exposed_scripts", _changed
    )
    tab.refresh_scripts()

    # 取消暴露的 a 既不能再出现在候选里，也不能再被执行。
    assert "a" not in _row_ids(tab)
    assert tab._checked_script_ids() == ["b"]
    assert [script.id for script in tab._checked_scripts()] == ["b"]
    assert [script.name for script in tab._checked_scripts()] == ["改过的名字"]


def test_script_rows_match_config_checkbox_density(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch)
    item = tab._script_list.topLevelItem(0)
    expected_height = tab._script_list.fontMetrics().height() + 12

    assert tab._script_list.topLevelItemCount() == 4
    assert item.sizeHint(0).height() == expected_height
    assert tab._script_list.header().minimumHeight() == 32


def test_config_rows_use_same_height_without_extra_layout_spacing(
    qtbot, monkeypatch
):
    tab, _ = _make_tab(qtbot, monkeypatch)
    config = BatchConfigItem(
        name="demo",
        columns=["role"],
        rows=[{"role": "甲"}, {"role": "乙"}],
    )
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_tab.load_batch_config",
        lambda: BatchConfig(active_config="demo", configs={"demo": config}),
    )

    tab._refresh_config_combo()
    tab._refresh_entry_list()

    expected_height = tab._script_list.fontMetrics().height() + 12
    assert [cb.height() for cb, _ in tab._entry_checkboxes] == [
        expected_height,
        expected_height,
    ]
    assert tab._entry_container.spacing() == 0


def test_batch_table_columns_are_compact_and_user_resizable(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch)
    tab._set_initial_column_widths()

    script_header = tab._script_list.header()
    progress_header = tab._progress_table.horizontalHeader()
    for header in (script_header, progress_header):
        assert all(
            header.sectionResizeMode(column) == QHeaderView.ResizeMode.Interactive
            for column in range(header.count())
        )

    assert tab._script_list.columnWidth(1) < tab._script_list.columnWidth(0)
    compact_width = (
        tab._progress_table.fontMetrics().horizontalAdvance("汉字宽度") + 20
    )
    assert tab._progress_table.columnWidth(0) == compact_width
    assert tab._progress_table.columnWidth(2) == compact_width
    assert sum(
        tab._progress_table.columnWidth(column) for column in range(3)
    ) <= tab._progress_table.viewport().width()
    assert (
        tab._progress_table.horizontalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )


def test_progress_cells_elide_and_expose_full_text_in_tooltips(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch)
    tab.resize(420, 320)
    tab._sub_tabs.setCurrentIndex(2)
    tab.show()
    config = BatchConfigItem(
        name="demo",
        user_column="role",
        columns=["role"],
        rows=[{"role": "很长的用户名"}],
    )
    scripts = [
        BatchScript(id="long", name="这是一个很长的脚本名称", wf_file="long.wf")
    ]

    enabled_rows = [(index, config.rows[0]) for index in range(12)]
    tab._build_progress_table(enabled_rows, config, scripts)
    qtbot.wait(1)
    tab._set_progress_column_widths()
    qtbot.wait(1)

    assert tab._progress_table.textElideMode() == Qt.TextElideMode.ElideRight
    assert tab._progress_table.item(0, 0).toolTip() == "很长的用户名"
    assert tab._progress_table.item(0, 1).toolTip() == "这是一个很长的脚本名称"
    assert tab._progress_table.item(0, 2).toolTip() == ST_PENDING
    assert tab._progress_table.horizontalScrollBar().maximum() == 0

    # Even explicit user resizing must redistribute columns instead of scrolling.
    tab._progress_table.setColumnWidth(1, 500)
    qtbot.wait(1)
    assert sum(
        tab._progress_table.columnWidth(column) for column in range(3)
    ) <= tab._progress_table.viewport().width()
    assert not tab._progress_table.horizontalScrollBar().isVisible()

    tab.resize(260, 320)
    qtbot.wait(1)
    assert sum(
        tab._progress_table.columnWidth(column) for column in range(3)
    ) <= tab._progress_table.viewport().width()
    assert not tab._progress_table.horizontalScrollBar().isVisible()


def test_unavailable_checked_script_is_kept_and_reported(qtbot, monkeypatch):
    """发现不到的已勾选脚本：要报警，且绝不能被顺手从 script_ids 抹掉。"""
    tab, saved_orders = _make_tab(
        qtbot, monkeypatch, ("b", "gone", "a")
    )

    # 不参与执行……
    assert tab._checked_script_ids() == ["b", "a"]
    assert [s.id for s in tab._checked_scripts()] == ["b", "a"]
    # ……但原位保留在落盘顺序里，脚本一恢复暴露就能回来。
    assert tab._merged_script_ids() == ["b", "gone", "a"]

    # 任何一次持久化都不能把它写没。
    _item_by_id(tab, "c").setCheckState(0, Qt.CheckState.Checked)
    assert "gone" in saved_orders[-1]


def test_unavailable_script_is_surfaced_in_the_log(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch, ("gone",))

    assert any("gone" in line for line in tab._test_host.logs)
