"""Profile key 定义内分组的编辑状态与保存边界。"""

from PyQt6.QtCore import Qt

import lvjiang.core.profile as profile_core
import lvjiang.core.profile.schema as profile_schema
from lvjiang.core.profile.models import MODEL_STOCK, StockKeyDef
from lvjiang.core.profile.schema import ProfileSchema
from lvjiang.ui.profile import settings_dialog
from lvjiang.ui.profile.settings_dialog import ProfileDefinitionDialog


def _open_dialog(qtbot, monkeypatch, definitions):
    schema = ProfileSchema(keys_by_model={MODEL_STOCK: definitions})
    monkeypatch.setattr(profile_core, "get_profile_config", lambda: schema)
    dialog = ProfileDefinitionDialog()
    qtbot.addWidget(dialog)
    return dialog, schema


def test_default_group_is_shown_in_chinese_and_cancel_keeps_config(qtbot, monkeypatch):
    original = StockKeyDef(key="coins", label="铜钱")
    dialog, schema = _open_dialog(qtbot, monkeypatch, [original])
    tab = dialog._tabs[MODEL_STOCK]

    assert tab._group_tabs.tabText(tab._group_tabs.currentIndex()) == "默认"
    assert tab._group_tabs.tabData(tab._group_tabs.currentIndex()) == "default"
    assert tab._group_tabs.usesScrollButtons()
    assert not tab._group_tabs.expanding()
    assert tab._group_tabs.elideMode() == Qt.TextElideMode.ElideNone
    assert tab.table.item(0, 1).text() == "默认"

    dialog._assign_key_group(MODEL_STOCK, dialog._drafts[MODEL_STOCK][0], "资产")

    assert schema.get_key("coins").group == "default"
    assert tab._group_tabs.tabText(tab._group_tabs.currentIndex()) == "资产"


def test_group_column_edit_redraws_groups_and_save_keeps_hidden_rows(qtbot, monkeypatch):
    definitions = [
        StockKeyDef(key="first", label="一"),
        StockKeyDef(key="second", label="二"),
    ]
    dialog, _schema = _open_dialog(qtbot, monkeypatch, definitions)
    tab = dialog._tabs[MODEL_STOCK]
    monkeypatch.setattr(
        settings_dialog.QInputDialog,
        "getText",
        lambda *_args, **_kwargs: ("资产", True),
    )

    dialog._edit_key_group(MODEL_STOCK, 1)

    assert [tab._group_tabs.tabText(i) for i in range(tab._group_tabs.count())] == [
        "默认",
        "资产",
    ]
    assert tab.table.rowCount() == 1
    assert tab.table.item(0, 0).text() == "second"

    saved = []
    monkeypatch.setattr(profile_schema, "save_profile_config", saved.append)
    dialog._on_save()

    stored = saved[0].get_keys_by_model(MODEL_STOCK)
    assert [(kd.key, kd.group) for kd in stored] == [
        ("first", "default"),
        ("second", "资产"),
    ]


def test_group_editor_only_opens_on_group_column_double_click(qtbot, monkeypatch):
    dialog, _schema = _open_dialog(
        qtbot,
        monkeypatch,
        [StockKeyDef(key="coins", label="铜钱")],
    )
    tab = dialog._tabs[MODEL_STOCK]
    calls = []
    monkeypatch.setattr(
        dialog,
        "_edit_key_group",
        lambda model_type, row: calls.append((model_type, row)),
    )

    tab.table.cellClicked.emit(0, 1)
    assert calls == []

    tab.table.cellDoubleClicked.emit(0, 1)
    assert calls == [(MODEL_STOCK, 0)]
