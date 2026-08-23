"""脚本录制 F12 全局热键的对话框级生命周期测试。"""

from unittest.mock import MagicMock

from PyQt6.QtCore import QEvent
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QApplication, QDialog, QMainWindow

from lvjiang.core import platforms
from lvjiang.ui.script_record_dialog import ScriptRecordDialog


class _Listener:
    def __init__(self):
        self.stopped = False
        self.join_timeout = None

    def stop(self):
        self.stopped = True

    def join(self, timeout):
        self.join_timeout = timeout

    def is_alive(self):
        return False


def _parent(qtbot):
    parent = QMainWindow()
    parent._running = False
    parent._backend = "adb"
    qtbot.addWidget(parent)
    return parent


def test_f12_is_registered_only_while_dialog_is_open(qtbot, monkeypatch):
    registrations = []
    listener = _Listener()

    def start(hotkeys):
        registrations.append(hotkeys)
        return listener

    monkeypatch.setattr(platforms, "start_global_hotkeys", start)
    dialog = ScriptRecordDialog(_parent(qtbot))
    qtbot.addWidget(dialog)

    assert registrations == []
    dialog.show()
    qtbot.waitUntil(lambda: len(registrations) == 1)
    assert set(registrations[0]) == {"<f12>"}

    spy = QSignalSpy(dialog.f12_pressed)
    registrations[0]["<f12>"]()
    assert len(spy) == 1

    # 用户切到游戏窗口时只会失去激活状态，F12 必须仍有效。
    QApplication.sendEvent(
        dialog, QEvent(QEvent.Type.WindowDeactivate))
    assert listener.stopped is False
    assert dialog._f12_hotkey_listener is listener

    dialog.done(QDialog.DialogCode.Rejected)
    qtbot.waitUntil(lambda: listener.stopped)
    assert listener.join_timeout == 3.0
    assert dialog._f12_hotkey_listener is None


def test_f12_registration_is_idempotent(qtbot, monkeypatch):
    start = MagicMock(return_value=_Listener())
    monkeypatch.setattr(platforms, "start_global_hotkeys", start)
    dialog = ScriptRecordDialog(_parent(qtbot))
    qtbot.addWidget(dialog)

    dialog._start_f12_hotkey()
    dialog._start_f12_hotkey()

    start.assert_called_once()
    dialog.stop_f12_hotkey()
