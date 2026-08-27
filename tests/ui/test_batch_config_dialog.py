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
