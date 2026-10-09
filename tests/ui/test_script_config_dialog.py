"""脚本配置按元数据分组，两页草稿共同保存且不改变入口选择。"""

from copy import deepcopy

import pytest
from PyQt6.QtCore import Qt

from lvjiang.ui.scripts.config_dialog import ScriptConfigDialog


@pytest.fixture
def panel(qtbot, monkeypatch):
    scripts = [
        {"id": sid, "name": name, "scope": scope, "class": "TestWorkflow"}
        for sid, name, scope in (
            ("a", "甲", "daily"), ("b", "乙", "dedicated"), ("c", "丙", "daily"),
        )
    ]
    daily = {
        "workflow_id": "a",
        "scripts": {"order": ["a", "b", "c"],
                    "scopes": {"a": "dedicated", "b": "daily"}},
    }

    class Store:
        def get_node(self, name, default):
            assert name == "daily"
            return deepcopy(daily)

        def update_node(self, name, values):
            assert name == "daily"
            daily.update(deepcopy(values))

    monkeypatch.setattr("lvjiang.core.config.get_session_store", lambda: Store())
    monkeypatch.setattr("lvjiang.ui.scripts.config_dialog.discover_scripts", lambda: scripts)
    dialog = ScriptConfigDialog(None, embedded=True)
    qtbot.addWidget(dialog)
    return dialog, daily


def _ids(dialog, scope):
    table = dialog._tables[scope]
    return [table.item(row, dialog.COL_NAME).data(Qt.ItemDataRole.UserRole)
            for row in range(table.rowCount())]


def test_tab_switch_preserves_drafts_and_undo_restores_both_groups(panel):
    dialog, daily = panel
    original = deepcopy(daily)
    assert _ids(dialog, "daily") == ["a", "c"]
    assert _ids(dialog, "dedicated") == ["b"]
    dialog._tabs.setCurrentIndex(1)
    dialog._tabs.setCurrentIndex(0)
    assert not dialog.dirty
    dialog._table.item(0, dialog.COL_NAME).setText("改名")
    dialog._tabs.setCurrentIndex(1)
    dialog._table.item(0, dialog.COL_EXPOSE).setCheckState(Qt.CheckState.Checked)
    dialog._tabs.setCurrentIndex(0)
    assert dialog._table.item(0, dialog.COL_NAME).text() == "改名"
    assert daily == original
    dialog._on_undo()
    assert not dialog.dirty
    assert dialog._tables["daily"].item(0, dialog.COL_NAME).text() == "甲"
    assert dialog._tables["dedicated"].item(0, dialog.COL_EXPOSE).checkState() == Qt.CheckState.Unchecked
    assert daily == original


def test_save_merges_both_tabs_and_preserves_cross_scope_order(panel):
    dialog, daily = panel
    dialog._table.item(0, dialog.COL_NAME).setText("改名")
    dialog._table.setCurrentCell(0, dialog.COL_NAME)
    dialog._move_row(1)
    dialog._tabs.setCurrentIndex(1)
    dialog._table.item(0, dialog.COL_EXPOSE).setCheckState(Qt.CheckState.Checked)
    dialog._on_save()
    assert daily == {
        "workflow_id": "a",
        "scripts": {"order": ["c", "b", "a"],
                    "visible": {"b": True}, "names": {"a": "改名"}},
    }
    assert not dialog.dirty
    dialog._load()
    assert _ids(dialog, "daily") == ["c", "a"]
    assert _ids(dialog, "dedicated") == ["b"]
    assert dialog._tables["daily"].item(1, dialog.COL_NAME).text() == "改名"
    assert dialog._tables["dedicated"].item(0, dialog.COL_EXPOSE).checkState() == Qt.CheckState.Checked
