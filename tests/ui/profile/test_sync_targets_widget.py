"""同步目标编辑使用级联 key 选择器，并正确回读 model:key。"""

from PyQt6.QtWidgets import QLineEdit, QPushButton

from lvjiang.core.profile.models import QuotaKeyDef, StockKeyDef, SyncTargetDef
from lvjiang.core.profile.schema import (
    ProfileSchema,
    reload_profile_config,
    save_profile_config,
)
from lvjiang.ui.profile.settings_dialog import _SyncTargetsWidget


def _leaf_texts(menu):
    texts = []
    for action in menu.actions():
        if action.menu() is not None:
            texts.extend(_leaf_texts(action.menu()))
        else:
            texts.append(action.text())
    return texts


def _find_action(menu, text):
    for action in menu.actions():
        if action.text() == text:
            return action
    return None


def test_sync_target_row_uses_cascade_picker_and_roundtrips(qapp, qtbot):
    save_profile_config(ProfileSchema(keys_by_model={
        "quota": [QuotaKeyDef(key="daily", label="每日")],
        "stock": [
            StockKeyDef(key="a", label="资源甲", group="资源"),
            StockKeyDef(key="b", label="资源乙", group="资源"),
            StockKeyDef(key="z", label="数量"),
        ],
    }))
    reload_profile_config()

    widget = _SyncTargetsWidget()
    qtbot.addWidget(widget)
    widget.add_row(SyncTargetDef(key="stock:a", ratio=2.0, source="导入"))

    picker = widget._table.cellWidget(0, 0)
    assert isinstance(picker, QPushButton)
    assert picker.text() == "资源甲 (a)"
    targets = widget.get_sync_targets()
    assert len(targets) == 1
    assert (targets[0].key, targets[0].ratio, targets[0].source) == (
        "stock:a", 2.0, "导入")

    # 级联菜单重选：库存 → 资源 → 资源乙，回读成命名空间 key。
    menu = picker.build_key_menu()
    stock_menu = _find_action(menu, "库存").menu()
    group_menu = _find_action(stock_menu, "资源").menu()
    _find_action(group_menu, "资源乙 (b)").trigger()
    assert widget.get_sync_targets()[0].key == "stock:b"


def test_sync_target_row_excludes_self_from_menu_and_load(qapp, qtbot):
    save_profile_config(ProfileSchema(keys_by_model={
        "stock": [
            StockKeyDef(key="a", label="资源甲", group="资源"),
            StockKeyDef(key="b", label="资源乙", group="资源"),
        ],
    }))
    reload_profile_config()

    key_input = QLineEdit("a")
    widget = _SyncTargetsWidget(exclude_key_input=key_input)
    qtbot.addWidget(widget)
    widget.add_row()
    picker = widget._table.cellWidget(0, 0)
    leaves = _leaf_texts(picker.build_key_menu())
    assert "资源甲 (a)" not in leaves
    assert "资源乙 (b)" in leaves

    # 既有定义指向自身时与旧平铺下拉一致：不展示、不回读。
    widget.add_row(SyncTargetDef(key="stock:a"))
    self_picker = widget._table.cellWidget(1, 0)
    assert self_picker.text() == "（请选择）"
    assert widget.get_sync_targets() == []
