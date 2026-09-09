"""Readonly UI restrictions are confined to the scene-manager entry."""

import pytest
from PyQt6.QtWidgets import QComboBox, QMainWindow

from lvjiang.core import access
from lvjiang.core.config import resolver as resolver_module


@pytest.fixture
def readonly(monkeypatch):
    monkeypatch.setattr(access, "_readonly", True)


@pytest.mark.parametrize("is_readonly", [False, True])
def test_scene_manager_action_keeps_f3_and_follows_instance_access(
        qtbot, monkeypatch, is_readonly):
    from lvjiang.ui.main.menu_ops import MenuOpsMixin

    class Host(MenuOpsMixin, QMainWindow):
        def _open_batch_config(self):
            pass

        def _check_update(self):
            pass

    monkeypatch.setattr(access, "_readonly", is_readonly)
    host = Host()
    qtbot.addWidget(host)
    host._setup_menu()
    action = next(action for action in host.menuBar().actions()[0].menu().actions()
                  if action.text() == "场景管理")
    assert action.shortcut().toString() == "F3"
    assert action.isEnabled() is not is_readonly


def test_script_editor_write_controls_are_not_instance_disabled(
        qtbot, readonly, tmp_path, monkeypatch):
    from lvjiang.ui.scripts.editor_dialog import ScriptEditorDialog

    resolver = resolver_module.ConfigResolver(tmp_path / "system", tmp_path / "local", dev_mode=True)
    monkeypatch.setattr(resolver_module, "_resolver", resolver)
    resolver.write_entity("workflows/demo.wf", 'log info "demo"\n')
    widget = ScriptEditorDialog()
    qtbot.addWidget(widget)
    assert widget.tree.isEnabled()
    assert widget.btn_check.isEnabled()
    for button in (widget.btn_new, widget.btn_save, widget.btn_save_as, widget.btn_delete):
        button.setEnabled(True)
        assert button.isEnabled()


def test_attribute_source_editors_are_not_instance_disabled(
        qtbot, readonly, tmp_path, monkeypatch):
    from lvjiang.apps.yysls.core.attr_model import AttrModelManager
    from lvjiang.apps.yysls.ui.game_settings import attr_source_panel as module

    path = tmp_path / "inner_way.yaml"
    path.write_text(
        "kind: inner_way\nentries:\n"
        "  A·一重: {modeled: false}\n  B·一重: {modeled: false}\n",
        encoding="utf-8",
    )
    before = path.read_bytes()
    manager = AttrModelManager(tmp_path)
    monkeypatch.setattr(module, "get_attr_model_manager", lambda: manager)
    widget = module.AttrSourcePanel(("inner_way",))
    qtbot.addWidget(widget)
    assert widget._search.isEnabled() and widget._list.isEnabled()
    widget._btn_add.setEnabled(True)
    widget._btn_del.setEnabled(True)
    assert widget._btn_add.isEnabled() and widget._btn_del.isEnabled()
    for row in (0, 1, 0):
        widget._list.setCurrentRow(row)
        mode = widget._table.cellWidget(0, 2)
        scope = widget._table.cellWidget(0, 4)
        assert isinstance(mode, QComboBox)
        mode.setEnabled(True)
        scope.setEnabled(True)
        assert mode.isEnabled() and scope.isEnabled()
    assert path.read_bytes() == before


@pytest.mark.parametrize("readonly", [False, True])
def test_equipment_slot_click_while_user_is_running(qtbot, tmp_path, monkeypatch, readonly):
    from types import MethodType, SimpleNamespace

    from lvjiang.apps.yysls.core.loadout.repository import LoadoutRepository
    from lvjiang.apps.yysls.ui.loadout.equip.status_tab import EquipStatusTab

    monkeypatch.setattr(access, "_readonly", readonly)
    repo = LoadoutRepository("alice", tmp_path)
    repo.upsert_item({"_fp": "ring", "type": "环"})
    before = repo.path.read_bytes()
    rebuilt = []
    tab = SimpleNamespace(
        _selected_slot=None, _slot_cards={}, _filters_collapsed=False,
        _inv=SimpleNamespace(_repo=repo), _rebuild_grid=lambda: rebuilt.append(True))
    for name in ("sort", "type", "level", "affix", "source", "quality", "status"):
        combo = QComboBox()
        qtbot.addWidget(combo)
        combo.addItem("全部", "all")
        if name == "type":
            combo.addItem("环", "ring")
        setattr(tab, f"_{name}_filter", combo)
    tab._save_filter_settings = MethodType(EquipStatusTab._save_filter_settings, tab)
    tab._save_user_filter = MethodType(EquipStatusTab._save_user_filter, tab)
    lease = access.acquire_user("alice", tmp_path)
    try:
        EquipStatusTab._on_slot_clicked(tab, "ring")
        assert tab._selected_slot == "ring"
        assert repo.get_ui_state("equip_filter")["type"] == "ring"
        EquipStatusTab._on_slot_clicked(tab, "ring")
        assert tab._selected_slot is None
        assert repo.get_ui_state("equip_filter")["type"] == "all"
    finally:
        lease.release()
    assert rebuilt == [True, True]
    assert repo.path.read_bytes() == before
