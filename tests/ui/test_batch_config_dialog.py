"""批量配置对话框的列 schema 与单条目生命周期选项。"""

from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QInputDialog, QMenu

from lvjiang.core.batch_config import BatchConfig, BatchConfigItem
from lvjiang.ui.batch.batch_config_dialog import BatchConfigDialog


def _dialog(qtbot, monkeypatch, item: BatchConfigItem) -> BatchConfigDialog:
    config = BatchConfig(
        configs={item.name: item},
        active_config=item.name,
    )
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.load_batch_config",
        lambda: config,
    )
    dialog = BatchConfigDialog()
    qtbot.addWidget(dialog)
    return dialog


def test_single_item_lifecycle_checkbox_loads_and_saves(qtbot, monkeypatch):
    item = BatchConfigItem(
        name="测试配置",
        skip_lifecycle_for_single_item=False,
    )
    dialog = _dialog(qtbot, monkeypatch, item)

    assert dialog._skip_single_lifecycle.isChecked() is False
    dialog._skip_single_lifecycle.setChecked(True)
    dialog._save_current_config()
    assert item.skip_lifecycle_for_single_item is True


def test_existing_role_schema_is_preserved_and_username_is_dropdown(
    qtbot, monkeypatch,
):
    item = BatchConfigItem(
        name="业务默认",
        columns=["role", "account"],
        rows=[{"role": "甲", "account": "A"}],
        user_column="role",
    )
    dialog = _dialog(qtbot, monkeypatch, item)

    assert dialog._defined_columns() == ["role", "account"]
    assert [
        dialog._user_column_combo.itemText(index)
        for index in range(dialog._user_column_combo.count())
    ] == ["role", "account"]
    assert dialog._user_column_combo.currentText() == "role"


def test_zero_column_legacy_config_gets_user_editing_entry(qtbot, monkeypatch):
    item = BatchConfigItem(name="异常旧配置", columns=[], user_column="")
    dialog = _dialog(qtbot, monkeypatch, item)

    assert dialog._defined_columns() == ["user"]
    assert dialog._user_column_combo.currentText() == "user"


def test_new_config_starts_with_decoupled_user_column(qtbot, monkeypatch):
    item = BatchConfigItem(name="已有", columns=["role"], user_column="role")
    dialog = _dialog(qtbot, monkeypatch, item)
    monkeypatch.setattr(
        QInputDialog, "getText", lambda *args, **kwargs: ("新配置", True)
    )

    dialog._on_new_config()

    created = dialog._cfg.configs["新配置"]
    assert created.columns == ["user"]
    assert created.user_column == "user"


def test_add_rename_and_delete_columns_preserve_data_and_username(
    qtbot, monkeypatch,
):
    item = BatchConfigItem(
        name="测试配置",
        columns=["role"],
        rows=[{"role": "甲"}],
        user_column="role",
    )
    dialog = _dialog(qtbot, monkeypatch, item)
    monkeypatch.setattr(
        QInputDialog, "getText", lambda *args, **kwargs: ("note", True)
    )

    dialog._add_column(0)
    assert dialog._defined_columns() == ["role", "note"]
    assert dialog._table.item(0, 0).text() == "甲"
    assert dialog._table.item(0, 1).text() == ""
    assert dialog._table.currentColumn() == 1

    assert dialog._rename_column(0, "character") is True
    assert dialog._user_column_combo.currentText() == "character"
    dialog._remove_column(0)
    assert dialog._defined_columns() == ["character", "note"]

    dialog._user_column_combo.setCurrentText("note")
    dialog._remove_column(0)
    assert dialog._defined_columns() == ["note"]
    dialog._save_current_config()
    assert item.user_column == "note"
    assert item.rows == [{"note": ""}]


def test_add_column_cancel_or_blank_does_not_change_schema(qtbot, monkeypatch):
    item = BatchConfigItem(
        name="测试配置", columns=["user"], user_column="user",
    )
    dialog = _dialog(qtbot, monkeypatch, item)
    responses = iter((("", False), ("   ", True)))
    monkeypatch.setattr(
        QInputDialog, "getText", lambda *args, **kwargs: next(responses)
    )

    dialog._add_column(0)
    dialog._add_column(0)

    assert dialog._defined_columns() == ["user"]


def test_add_column_rejects_duplicate_name(qtbot, monkeypatch):
    item = BatchConfigItem(
        name="测试配置", columns=["user"], user_column="user",
    )
    dialog = _dialog(qtbot, monkeypatch, item)
    warnings = []
    monkeypatch.setattr(
        QInputDialog, "getText", lambda *args, **kwargs: ("user", True)
    )
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.QMessageBox.warning",
        lambda *args: warnings.append(args),
    )

    dialog._add_column(0)

    assert dialog._defined_columns() == ["user"]
    assert len(warnings) == 1


def test_header_double_click_renames_column(qtbot, monkeypatch):
    item = BatchConfigItem(
        name="测试配置", columns=["user"], user_column="user",
    )
    dialog = _dialog(qtbot, monkeypatch, item)
    monkeypatch.setattr(
        QInputDialog, "getText", lambda *args, **kwargs: ("role", True)
    )

    dialog._on_header_double_clicked(0)

    assert dialog._defined_columns() == ["role"]
    assert dialog._user_column_combo.currentText() == "role"


def test_column_rename_rejects_empty_and_duplicate_names(qtbot, monkeypatch):
    item = BatchConfigItem(
        name="测试配置", columns=["user", "note"], user_column="user",
    )
    dialog = _dialog(qtbot, monkeypatch, item)
    warnings = []
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.QMessageBox.warning",
        lambda *args: warnings.append(args),
    )

    assert dialog._rename_column(1, " ") is False
    assert dialog._rename_column(1, "user") is False
    assert dialog._defined_columns() == ["user", "note"]
    assert len(warnings) == 2


def test_username_column_delete_action_is_disabled(qtbot, monkeypatch):
    item = BatchConfigItem(
        name="测试配置", columns=["user", "note"], user_column="user",
    )
    dialog = _dialog(qtbot, monkeypatch, item)
    captured = []
    monkeypatch.setattr(
        QMenu, "exec", lambda menu, *args: captured.extend(menu.actions())
    )

    dialog._on_header_context_menu(QPoint(5, 5))

    delete_action = next(
        action for action in captured if action.text() == "删除当前列"
    )
    assert delete_action.isEnabled() is False


# ─── 行勾选状态随行搬运 ──────────────────────────────────


def _rows_dialog(qtbot, monkeypatch, flags):
    """三行配置 + 给定的勾选状态，返回 (dialog, saved) 。"""
    item = BatchConfigItem(
        name="账号表",
        columns=["role"],
        user_column="role",
        rows=[{"role": "甲"}, {"role": "乙"}, {"role": "丙"}],
    )
    saved: dict[str, list[bool]] = {}
    monkeypatch.setattr(
        "lvjiang.core.batch_config.load_enabled_rows",
        lambda: {item.name: list(flags)},
    )
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.load_enabled_rows",
        lambda: {item.name: list(flags)},
    )
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.save_enabled_rows",
        lambda value: saved.update(value),
    )
    return _dialog(qtbot, monkeypatch, item), saved, item


def test_moving_a_row_carries_its_enabled_flag(qtbot, monkeypatch):
    """乙 被禁用；把 丙 上移一格后，禁用的仍必须是 乙，不能挪到别人头上。"""
    dialog, saved, item = _rows_dialog(
        qtbot, monkeypatch, [True, False, True]
    )

    dialog._table.setCurrentCell(2, 0)   # 选中 丙
    dialog._move_row(-1)                 # 丙 ↑ → 甲 丙 乙
    dialog._save_current_config()

    assert [row["role"] for row in item.rows] == ["甲", "丙", "乙"]
    # 勾选状态跟着行走：仍然只有 乙 是禁用的。
    assert saved["账号表"] == [True, True, False]


def test_deleting_a_row_removes_its_flag_not_a_neighbour_s(qtbot, monkeypatch):
    """删掉 甲 后，禁用的仍必须是 乙。"""
    dialog, saved, item = _rows_dialog(
        qtbot, monkeypatch, [True, False, True]
    )

    dialog._table.setCurrentCell(0, 0)
    dialog._on_delete_row()
    dialog._save_current_config()

    assert [row["role"] for row in item.rows] == ["乙", "丙"]
    assert saved["账号表"] == [False, True]


def test_added_row_defaults_to_enabled(qtbot, monkeypatch):
    dialog, saved, item = _rows_dialog(
        qtbot, monkeypatch, [True, False, True]
    )

    dialog._on_add_row()
    dialog._save_current_config()

    assert saved["账号表"] == [True, False, True, True]


def test_save_does_not_revert_concurrent_script_id_changes(qtbot, monkeypatch):
    """对话框开着时热键启动批量写了 script_ids，保存不能把它退回去。"""
    item = BatchConfigItem(name="账号表", columns=["role"], rows=[])
    opened = BatchConfig(
        configs={item.name: item}, active_config=item.name, script_ids=["旧"]
    )
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.load_batch_config",
        lambda: opened,
    )
    dialog = BatchConfigDialog()
    qtbot.addWidget(dialog)

    # 对话框打开期间，别处把 script_ids 改了。
    latest = BatchConfig(
        configs={item.name: item}, active_config=item.name,
        script_ids=["新A", "新B"],
    )
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.load_batch_config",
        lambda: latest,
    )
    saved: list[BatchConfig] = []
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.save_batch_config", saved.append
    )
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.save_enabled_rows",
        lambda _v: None,
    )
    monkeypatch.setattr(
        "lvjiang.ui.batch.batch_config_dialog.load_enabled_rows", dict
    )

    dialog._on_save()

    assert saved[-1].script_ids == ["新A", "新B"]
    assert saved[-1].configs is opened.configs
