"""清单 UI 的确定行为：声明目标不能被回显改写，排序和进度不改变业务语义。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtWidgets import QDialog, QWidget

from lvjiang.apps.yysls.core.linger import overrides
from lvjiang.apps.yysls.core.linger.models import catalog_from_doc
from lvjiang.apps.yysls.ui.linger.dialog import COL_ITEM, LingerDialog
from lvjiang.apps.yysls.ui.linger.item_dialog import LingerItemDialog, candidate_targets
from lvjiang.core.config.resolver import get_resolver
from lvjiang.core.profile import schema
from lvjiang.core.profile.service import profile_read


class _Host(QWidget):
    def active_user_name(self) -> str:
        return "test-user"


@pytest.fixture
def dialog(qtbot, tmp_path: Path, monkeypatch):
    from lvjiang import constants

    # 全局夹具隔离 system/local/session；用户文档还有自己的动态路径。
    monkeypatch.setattr(constants, "USERS_DIR", tmp_path / "session" / "users")
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump({"stock": [
        {"key": "niaoniao", "label": "库存甲"},
        {"key": "other", "label": "库存乙"},
    ]}, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(schema, "_PROFILE_PATH", path)
    schema._config = None
    host = _Host()
    qtbot.addWidget(host)
    window = LingerDialog(host)
    qtbot.addWidget(window)
    window.show()
    return window


def _row(dialog, key: str) -> int:
    return next(row for row, ref in dialog._rows.items()
                if ref.item.id == key and ref.slot is None)


def _item(key: str):
    return overrides.load_effective_catalog().item(key)[1]


def _edit(dialog, key: str, monkeypatch, change) -> None:
    def accept(editor):
        change(editor)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(LingerItemDialog, "exec", accept)
    dialog._select_item(key)
    dialog.edit_button.click()


@pytest.mark.parametrize("old,new", [
    ("stock:other", "stock:niaoniao"),
    ("stock:niaoniao", "stock:other"),
])
def test_edit_preserves_follow_default_and_books_to_new_target(dialog, monkeypatch, old, new):
    """内置默认与自选默认都不能被普通编辑变成显式绑定，更不能写进旧库存。"""
    overrides.set_target_candidates([old, new], old)
    key = overrides.add_item("qinghe", {"label": "测试来源", "count": 1, "target": ""})
    dialog._refresh_data()

    def rename(editor):
        editor.label_edit.setText("测试改名")
        editor.detail_edit.setPlainText("只修改说明，不改变目标")

    _edit(dialog, key, monkeypatch, rename)
    local = get_resolver().load_local(overrides.LINGER_FILE)
    entry = next(item for item in local["qinghe"]["items"]["__added__"] if item["id"] == key)
    assert "target" not in entry
    overrides.set_target_candidates([old, new], new)
    dialog._refresh_data()
    assert _item(key).target == new
    dialog.booking_switch.setChecked(True)
    dialog.table.item(_row(dialog, key), COL_ITEM).setCheckState(Qt.CheckState.Checked)
    assert profile_read("test-user", new.partition(":")[2]) == 1
    assert profile_read("test-user", old.partition(":")[2]) is None


def test_editor_preserves_explicit_targets_and_can_return_to_follow(dialog, monkeypatch):
    """none、显式等于默认和继承不能混淆；非目标字段编辑与主动切换分别处理。"""
    default = "stock:niaoniao"
    overrides.set_target_candidates([default, "stock:other", "none"], default)
    inherited = overrides.add_item("qinghe", {"label": "跟随", "count": 1})
    explicit = overrides.add_item("qinghe", {"label": "显式默认", "count": 1, "target": default})
    none = overrides.add_item("qinghe", {"label": "不入账", "count": 1, "target": "none"})
    dialog._refresh_data()
    for key, expected in ((inherited, ""), (explicit, default), (none, "none")):
        def change_kind(editor, expected=expected):
            assert editor.target_combo.currentData() == expected
            editor.kind_combo.setCurrentIndex(editor.kind_combo.findData("roster"))
            assert editor.target_combo.currentData() == expected
        _edit(dialog, key, monkeypatch, change_kind)
    overrides.set_default_target("stock:other")
    assert _item(inherited).target == "stock:other"
    assert _item(explicit).target == default
    assert _item(none).target == "none"

    def follow(editor):
        editor.target_combo.setCurrentIndex(editor.target_combo.findData(""))
    _edit(dialog, explicit, monkeypatch, follow)
    assert _item(explicit).target == "stock:other"

    def bind(editor):
        editor.target_combo.setCurrentIndex(editor.target_combo.findData(default))
    _edit(dialog, inherited, monkeypatch, bind)
    assert _item(inherited).target == default


def test_new_editor_starts_following_default(dialog, qtbot):
    editor = LingerItemDialog(list(dialog._catalog.regions))
    qtbot.addWidget(editor)
    editor.label_edit.setText("新来源")
    assert editor.target_combo.currentData() == ""
    key = overrides.add_item(editor.region_id(), editor.values())
    overrides.set_default_target("stock:other")
    assert _item(key).target == "stock:other"


@dataclass
class _DropEvent:
    """Qt 的 source() 无公共 setter；只适配事件外壳，生产落点/排序逻辑照常运行。"""
    table: object
    pos: QPointF
    accepted: bool = False

    def source(self):
        return self.table

    def position(self):
        return self.pos

    def acceptProposedAction(self):
        self.accepted = True

    def ignore(self):
        self.accepted = False


def _drop(dialog, source: str, target: str, *, below: bool = False):
    dialog.table.setCurrentCell(_row(dialog, source), COL_ITEM)
    cell = dialog.table.item(_row(dialog, target), COL_ITEM)
    dialog.table.scrollToItem(cell)
    rect = dialog.table.visualItemRect(cell)
    position = rect.center()
    position.setY(rect.bottom() - 2 if below else rect.top() + 2)
    event = _DropEvent(dialog.table, QPointF(position))
    dialog.table.dropEvent(event)
    return event


@pytest.mark.parametrize("filtered", [False, True])
def test_drag_to_region_end_uses_same_region_anchor(dialog, filtered):
    region = dialog._catalog.regions[0]
    if filtered:
        dialog.region_list.setCurrentRow(1)
    source = region.items[0].id
    assert _drop(dialog, source, region.items[-1].id, below=True).accepted
    assert overrides.load_effective_catalog().region(region.id).items[-1].id == source


@pytest.mark.parametrize("below", [False, True])
def test_drop_onto_itself_keeps_order_and_configuration(dialog, below):
    region = dialog._catalog.regions[0]
    before = get_resolver().load_local(overrides.LINGER_FILE)
    order = [item.id for item in region.items]
    assert not _drop(dialog, order[0], order[0], below=below).accepted
    assert [item.id for item in overrides.load_effective_catalog().region(region.id).items] == order
    assert get_resolver().load_local(overrides.LINGER_FILE) == before


def test_drag_normal_and_cross_region(dialog):
    first, second = dialog._catalog.regions[:2]
    a, b = first.items[:2]
    assert _drop(dialog, b.id, a.id).accepted
    assert overrides.load_effective_catalog().region(first.id).items[0].id == b.id
    before = get_resolver().load_local(overrides.LINGER_FILE)
    assert not _drop(dialog, b.id, second.items[-1].id, below=True).accepted
    assert get_resolver().load_local(overrides.LINGER_FILE) == before


def test_region_progress_excludes_periodic_entries_on_reload_and_refresh(dialog):
    """固定进度的分子分母必须同域；历史记录残留不能把周期计数混入左栏。"""
    region = next(r for r in dialog._catalog.regions if any(i.periodic for i in r.items))
    periodic = next(i for i in region.items if i.periodic)
    fixed = next(i for i in region.items if not i.periodic)
    dialog._state.set_done(periodic.id, 1, periodic.count)
    dialog._state.set_done(fixed.id, 1, fixed.count)
    official = catalog_from_doc(get_resolver().load_system(overrides.LINGER_FILE), overrides.LINGER_FILE)
    total = official.region(region.id).fixed_total
    dialog._refresh_region_list()
    entry = next(dialog.region_list.item(i) for i in range(dialog.region_list.count())
                 if dialog.region_list.item(i).data(Qt.ItemDataRole.UserRole) == region.id)
    assert entry.text() == f"{region.label}  1/{total}"
    dialog._refresh_region_labels(region)
    assert entry.text() == f"{region.label}  1/{total}"


def test_none_candidate_is_listed_once_in_editor(dialog, qtbot):
    overrides.set_target_candidates(["stock:niaoniao", "none"], "stock:niaoniao")
    assert [value for value, _ in candidate_targets()].count("none") == 1
    editor = LingerItemDialog(list(dialog._catalog.regions))
    qtbot.addWidget(editor)
    assert [editor.target_combo.itemData(i) for i in range(editor.target_combo.count())].count("none") == 1


def test_sync_model_dialog_rejects_unchecked_default_and_realigns_on_uncheck(dialog, qtbot):
    from lvjiang.apps.yysls.ui.linger.sync_model_dialog import LingerSyncModelDialog

    overrides.set_target_candidates(["stock:niaoniao"], "stock:niaoniao")
    sync_dlg = LingerSyncModelDialog(dialog)
    qtbot.addWidget(sync_dlg)
    sync_dlg.show()

    # 1) 勾 A 后取消 A：设为默认必须禁用且默认值保持不变
    other_box = sync_dlg._checkboxes["stock:other"]
    other_box.setChecked(True)
    assert sync_dlg.use_default_button.isEnabled()
    other_box.setChecked(False)
    assert not sync_dlg.use_default_button.isEnabled()
    sync_dlg._set_default_from_selection()
    assert overrides.read_default_target() == "stock:niaoniao"

    # 2) 勾 A 并存为候选后取消当前默认项：默认自动切到剩余已勾选项且文本框同步刷新
    other_box.setChecked(True)
    sync_dlg._checkboxes["stock:niaoniao"].setChecked(False)
    assert overrides.read_default_target() == "stock:other"
    assert sync_dlg.default_edit.text() == "stock:other"
