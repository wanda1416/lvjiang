"""只读实例仅限制全局热键注册与账户管理；窗口内按键与其余功能入口保持可用。"""

from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QMainWindow

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
