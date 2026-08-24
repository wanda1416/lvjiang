"""Batch script selection order and row-density tests."""

from __future__ import annotations

from PyQt6.QtCore import QObject, Qt, pyqtSignal

from lvjiang.core.batch_config import BatchConfig
from lvjiang.core.config.models import UserConfig
from lvjiang.ui.batch.batch_tab import BatchTab


class _Host(QObject):
    automation_state_changed = pyqtSignal(str)
    is_running = False

    def __init__(self):
        super().__init__()
        self._user_config = UserConfig()

    def append_log(self, _message: str) -> None:
        pass


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


def test_script_rows_match_config_checkbox_density(qtbot, monkeypatch):
    tab, _ = _make_tab(qtbot, monkeypatch)
    item = tab._script_list.topLevelItem(0)

    assert tab._script_list.topLevelItemCount() == 4
    assert item.sizeHint(0).height() == 32
    assert tab._script_list.header().minimumHeight() == 32
