"""只读实例仅限制全局热键注册与账户管理；窗口内按键与其余功能入口保持可用。"""

from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QComboBox, QMainWindow

from lvjiang.core import access


@pytest.fixture
def readonly(monkeypatch):
    monkeypatch.setattr(access, "_readonly", True)


def test_scene_manager_action_keeps_f3_on_readonly_instance(
        qtbot, monkeypatch):
    from lvjiang.ui.main.menu_ops import MenuOpsMixin

    class Host(MenuOpsMixin, QMainWindow):
        def _open_batch_config(self):
            pass

        def _check_update(self):
            pass

    monkeypatch.setattr(access, "_readonly", True)
    host = Host()
    qtbot.addWidget(host)
    host._setup_menu()
    action = next(action for action in host.menuBar().actions()[0].menu().actions()
                  if action.text() == "场景管理")
    assert action.isEnabled()
    assert action.shortcut().toString() == "F3"


def test_readonly_hotkey_labels_hide_key_names(readonly):
    from lvjiang.ui.main.window import MainWindow

    assert MainWindow._hotkey_label("结束", "F10") == "结束"
    assert MainWindow._hotkey_status(
        "运行中", ("F10", "结束"), ("F11", "暂停")) == "运行中"


def test_readonly_main_window_keeps_application_hotkeys(qtbot, readonly):
    from lvjiang.core.config import HotkeyConfig
    from lvjiang.ui.main.window import MainWindow

    class Host(MainWindow):
        def __init__(self):
            QMainWindow.__init__(self)
            self._user_config = SimpleNamespace(hotkeys=HotkeyConfig())
            self._hotkey_listener = None
            self.calls = []

        def _request_stop(self, *args, **kwargs):
            self.calls.append("stop")

    host = Host()
    qtbot.addWidget(host)
    event = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_F10,
        Qt.KeyboardModifier.NoModifier)
    host.keyPressEvent(event)
    assert host.calls == ["stop"]


def test_readonly_record_dialog_ignores_application_hotkey(qtbot, readonly):
    from lvjiang.core.config import HotkeyConfig
    from lvjiang.ui.scripts.record_dialog import ScriptRecordDialog

    class Dialog(ScriptRecordDialog):
        def __init__(self):
            from PyQt6.QtWidgets import QDialog
            QDialog.__init__(self)
            self._main = SimpleNamespace(
                _user_config=SimpleNamespace(hotkeys=HotkeyConfig()))
            self._f12_hotkey_listener = None
            self.calls = []

        def toggle_recording(self):
            self.calls.append("record")

    dialog = Dialog()
    qtbot.addWidget(dialog)
    event = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_F12,
        Qt.KeyboardModifier.NoModifier)
    dialog.keyPressEvent(event)
    assert dialog.calls == []


def test_script_editor_write_controls_follow_normal_rules_on_readonly_instance(
        qtbot, tmp_path, monkeypatch):
    """只读实例不再额外禁用脚本编辑的写入按钮，状态与主实例完全一致。"""
    from lvjiang.ui.scripts.editor_dialog import ScriptEditorDialog

    def button_states():
        widget = ScriptEditorDialog()
        qtbot.addWidget(widget)
        return {name: getattr(widget, name).isEnabled()
                for name in ("btn_new", "btn_save", "btn_save_as", "btn_delete")}

    monkeypatch.setattr(access, "_readonly", False)
    primary = button_states()
    monkeypatch.setattr(access, "_readonly", True)
    assert button_states() == primary


def test_readonly_instance_does_not_start_main_global_hotkeys(
        readonly, monkeypatch):
    from lvjiang.core import platforms
    from lvjiang.ui.main.window import MainWindow

    calls = []
    monkeypatch.setattr(
        platforms, "start_global_hotkeys", lambda bindings: calls.append(bindings))
    host = SimpleNamespace(_main_global_hotkey_bindings=lambda: {"<f9>": object()})

    assert MainWindow._start_main_global_hotkeys(host) is None
    assert calls == []


def test_global_hotkey_factory_rejects_readonly_instance(readonly):
    from lvjiang.core.platforms import start_global_hotkeys

    assert start_global_hotkeys({"<f9>": lambda: None}) is None


def test_readonly_record_dialog_does_not_start_global_hotkey(
        readonly, monkeypatch):
    from lvjiang.core import platforms
    from lvjiang.ui.scripts.record_dialog import ScriptRecordDialog

    calls = []
    monkeypatch.setattr(
        platforms, "start_global_hotkeys", lambda bindings: calls.append(bindings))
    dialog = SimpleNamespace(
        _f12_hotkey_listener=None,
        _record_key="F12",
        f12_pressed=SimpleNamespace(emit=lambda: None),
    )

    ScriptRecordDialog._start_f12_hotkey(dialog)
    assert dialog._f12_hotkey_listener is None
    assert calls == []


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
    from lvjiang.core.user_config import User, save_user_metadata

    save_user_metadata(User("alice"), tmp_path)
    repo = LoadoutRepository("alice", tmp_path)
    repo.upsert_item({"_fp": "ring", "type": "环"})
    monkeypatch.setattr(access, "_readonly", readonly)
    before = repo.path.read_bytes()
    rebuilt = []
    tab = SimpleNamespace(
        _selected_slot=None, _slot_cards={}, _filters_collapsed=False,
        _inv=SimpleNamespace(_repo=repo), _rebuild_grid=lambda: rebuilt.append(True))
    for name in (
        "sort", "type", "level", "affix", "source", "quality", "status",
        "scan_time",
    ):
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
