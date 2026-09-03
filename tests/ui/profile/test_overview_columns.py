from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import PyQt6.QtWidgets as qt_widgets
import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem

import lvjiang.core.profile as profile_core
import lvjiang.ui.profile.column_management as column_management
import lvjiang.ui.profile.tab as profile_tab
from lvjiang.core.profile.models import MODEL_NOTE, NoteKeyDef
from lvjiang.ui.profile.column_management import ProfileColumnMixin
from lvjiang.ui.profile.settings_dialog import ProfileDefinitionDialog
from lvjiang.ui.profile.tab import ProfileOverviewTable, ProfileTab


class _Schema:
    def __init__(self, keys: set[str]):
        self._keys = keys

    def get_key(self, key: str):
        if key not in self._keys:
            return None
        return SimpleNamespace(key=key, label=key, sync_targets=[])

    def get_model_type(self, _key: str):
        return "note"


class _RefreshHost:
    def __init__(self):
        self._visible_column_keys: dict[str, list[str]] = {}
        self._loading = False

    def _load_all_users(self):
        return {}

    def _restore_column_widths(self, _group_name, _table):
        pass


def test_refresh_with_missing_schema_does_not_erase_configured_columns(
    qtbot, monkeypatch,
):
    groups = {
        "默认": {"columns": ["level", "money"]},
        "物资": {"columns": ["wood"]},
    }
    original = deepcopy(groups)
    monkeypatch.setattr(profile_tab, "get_groups", lambda: groups)
    for command in (
        "create_overview_group", "rename_overview_group", "remove_overview_group",
    ):
        monkeypatch.setattr(
            profile_tab, command,
            lambda *_args, name=command: pytest.fail(f"read path called {name}"),
        )
    monkeypatch.setattr(profile_core, "get_profile_config", lambda: _Schema(set()))
    table = QTableWidget()
    qtbot.addWidget(table)
    host = _RefreshHost()

    ProfileTab._refresh_group(host, "默认", table)

    assert groups == original
    assert host._visible_column_keys["默认"] == []
    assert table.columnCount() == 1  # schema 恢复前仅显示用户名列


def test_refresh_hides_only_missing_keys_without_changing_storage(qtbot, monkeypatch):
    groups = {"默认": {"columns": ["missing", "money"]}}
    monkeypatch.setattr(profile_tab, "get_groups", lambda: groups)
    monkeypatch.setattr(profile_core, "get_profile_config", lambda: _Schema({"money"}))
    table = QTableWidget()
    qtbot.addWidget(table)
    host = _RefreshHost()

    ProfileTab._refresh_group(host, "默认", table)

    assert groups["默认"]["columns"] == ["missing", "money"]
    assert host._visible_column_keys["默认"] == ["money"]
    assert table.columnCount() == 2


def test_empty_configuration_builds_transient_default_group_without_writing(
    qtbot, monkeypatch,
):
    monkeypatch.setattr(profile_tab, "get_groups", lambda: {})
    monkeypatch.setattr(profile_tab, "get_active_group", lambda: "")
    monkeypatch.setattr(profile_tab, "set_active_group", lambda _name: None)
    monkeypatch.setattr(profile_core, "get_profile_config", lambda: _Schema(set()))
    monkeypatch.setattr(ProfileTab, "_connect_profile_engine", lambda _self: None)
    for command in (
        "create_overview_group", "rename_overview_group", "remove_overview_group",
    ):
        monkeypatch.setattr(
            profile_tab, command,
            lambda *_args, name=command: pytest.fail(f"load path called {name}"),
        )
    host = SimpleNamespace(
        user_manager=SimpleNamespace(list_users=lambda: []),
    )

    tab = ProfileTab(host)
    qtbot.addWidget(tab)

    assert tab._tab_widget.count() == 1
    assert tab._tab_widget.tabText(0) == "默认"


class _ColumnHost(ProfileColumnMixin):
    def __init__(self, table: QTableWidget):
        self._visible_column_keys = {"默认": ["left", "right"]}
        self._tables = {"默认": table}
        self._loading = False
        self._reordering = False
        self.groups_rebuilt = False

    def _refresh_group(self, _group_name, _table):
        pass

    def _remove_column_width(self, _group_name, _data_idx, _table):
        pass

    def _build_groups(self):
        self.groups_rebuilt = True


def test_reorder_preserves_schema_keys_that_are_temporarily_hidden(qtbot, monkeypatch):
    groups = {"默认": {"columns": ["hidden_a", "left", "hidden_b", "right"]}}
    reordered: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(column_management, "get_groups", lambda: groups)
    monkeypatch.setattr(
        column_management, "reorder_overview_columns",
        lambda group, columns: reordered.append((group, list(columns))),
    )
    table = QTableWidget(0, 3)
    qtbot.addWidget(table)
    header = table.horizontalHeader()
    assert header is not None
    header.moveSection(2, 1)
    host = _ColumnHost(table)

    host._on_columns_reordered("默认", table)

    assert reordered[-1] == ("默认", [
        "hidden_a", "right", "hidden_b", "left",
    ])


def test_remove_visible_column_does_not_remove_preceding_hidden_key(qtbot, monkeypatch):
    groups = {"默认": {"columns": ["hidden", "left", "right"]}}
    removed: list[tuple[str, str]] = []
    monkeypatch.setattr(column_management, "get_groups", lambda: groups)
    monkeypatch.setattr(
        column_management, "remove_overview_column",
        lambda group, key: removed.append((group, key)),
    )
    table = QTableWidget(0, 3)
    qtbot.addWidget(table)
    host = _ColumnHost(table)

    host._remove_column("默认", 0)

    assert removed == [("默认", "left")]


def test_data_column_header_menu_includes_direct_definition_edit(qtbot, monkeypatch):
    actions: list[str] = []

    class _Menu:
        def __init__(self, _parent):
            pass

        def addAction(self, text, _callback=None):  # noqa: N802
            actions.append(text)

        def addSeparator(self):  # noqa: N802
            pass

        def exec(self, _pos):
            pass

    monkeypatch.setattr(qt_widgets, "QMenu", _Menu)
    table = QTableWidget(0, 3)
    table.resize(480, 240)
    table.show()
    qtbot.addWidget(table)
    host = _ColumnHost(table)
    header = table.horizontalHeader()
    assert header is not None
    x = header.sectionViewportPosition(1) + 5

    host._on_header_context_menu(QPoint(x, 5), "默认")

    assert actions == ["编辑当前列", "右侧新增列", "删除当前列"]


def test_edit_current_column_saves_only_that_definition(qtbot, monkeypatch):
    original = NoteKeyDef(key="left", label="旧标签")
    untouched = NoteKeyDef(key="right", label="保持不变")
    edited = NoteKeyDef(key="left", label="新标签")

    class _EditableSchema:
        def get_key(self, key):
            return original if key == "left" else untouched if key == "right" else None

        def get_model_type(self, key):
            return MODEL_NOTE if key in {"left", "right"} else None

        def get_all_keys(self):
            return [original, untouched]

        def get_keys_by_model(self, model_type):
            return [original, untouched] if model_type == MODEL_NOTE else []

    editor_calls = []

    def _edit(parent, model_type, key_def, known_keys, *, lock_key=False):
        editor_calls.append((parent, model_type, key_def, known_keys, lock_key))
        return edited

    saved = []
    reloaded = []
    monkeypatch.setattr(column_management, "get_profile_config", _EditableSchema)
    monkeypatch.setattr(ProfileDefinitionDialog, "open_key_editor", _edit)
    monkeypatch.setattr(column_management, "save_profile_config", saved.append)
    monkeypatch.setattr(
        column_management, "reload_profile_config", lambda: reloaded.append(True))
    table = QTableWidget(0, 3)
    qtbot.addWidget(table)
    host = _ColumnHost(table)

    host._edit_column_definition("默认", 0)

    assert editor_calls == [
        (host, MODEL_NOTE, original, {"left", "right"}, True),
    ]
    assert len(saved) == 1
    assert saved[0].get_keys_by_model(MODEL_NOTE) == [edited, untouched]
    assert reloaded == [True]
    assert host.groups_rebuilt


def test_direct_key_editor_locks_key_identity(qtbot, monkeypatch):
    parent = QTableWidget()
    qtbot.addWidget(parent)
    read_only = []

    def _inspect_dialog(dialog):
        key_input = next(
            widget for widget in dialog.findChildren(qt_widgets.QLineEdit)
            if widget.text() == "left"
        )
        read_only.append(key_input.isReadOnly())
        return 0

    monkeypatch.setattr(qt_widgets.QDialog, "exec", _inspect_dialog)

    result = ProfileDefinitionDialog.open_key_editor(
        parent,
        MODEL_NOTE,
        NoteKeyDef(key="left", label="标签"),
        {"left", "right"},
        lock_key=True,
    )

    assert result is None
    assert read_only == [True]


def _build_overview_table(qtbot) -> ProfileOverviewTable:
    table = ProfileOverviewTable()
    table.setColumnCount(3)
    table.setRowCount(2)
    table.setHorizontalHeaderLabels(["用户名", "等级", "备注"])
    values = [
        ["alice", "10", "甲"],
        ["bob", "20", "乙"],
    ]
    for row, row_values in enumerate(values):
        for column, value in enumerate(row_values):
            table.setItem(row, column, QTableWidgetItem(value))
    table.resize(480, 240)
    table.show()
    qtbot.addWidget(table)
    return table


def test_clicking_username_selects_and_copies_whole_row(qtbot):
    table = _build_overview_table(qtbot)

    qtbot.mouseClick(
        table.viewport(),
        Qt.MouseButton.LeftButton,
        pos=table.visualItemRect(table.item(1, 0)).center(),
    )

    assert {(index.row(), index.column()) for index in table.selectedIndexes()} == {
        (1, 0), (1, 1), (1, 2),
    }
    qtbot.keyClick(table, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QApplication.clipboard().text() == "bob\t20\t乙"


def test_clicking_header_selects_and_copies_whole_column(qtbot):
    table = _build_overview_table(qtbot)
    header = table.horizontalHeader()
    x = header.sectionViewportPosition(1) + header.sectionSize(1) // 2

    qtbot.mouseClick(
        header.viewport(),
        Qt.MouseButton.LeftButton,
        pos=QPoint(x, header.height() // 2),
    )

    assert {(index.row(), index.column()) for index in table.selectedIndexes()} == {
        (0, 1), (1, 1),
    }
    qtbot.keyClick(table, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QApplication.clipboard().text() == "10\n20"


def test_ctrl_a_then_ctrl_c_copies_whole_table(qtbot):
    table = _build_overview_table(qtbot)
    table.setFocus()

    qtbot.keyClick(table, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    qtbot.keyClick(table, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)

    assert QApplication.clipboard().text() == "alice\t10\t甲\nbob\t20\t乙"
