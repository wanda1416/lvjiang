"""脚本录制 F12 全局热键的对话框级生命周期测试。"""

from unittest.mock import MagicMock

from PyQt6.QtCore import QEvent
from PyQt6.QtTest import QSignalSpy
from PyQt6.QtWidgets import QApplication, QDialog, QMainWindow

from lvjiang.core import platforms
from lvjiang.core.input_trace import InputTrace, InputTraceEvent
from lvjiang.ui import script_record_dialog as dialog_module
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


def test_precision_defaults_to_low_and_follows_radio_selection(qtbot):
    """precision 属性由 radio 单选状态决定，默认低精度。"""
    dialog = ScriptRecordDialog(_parent(qtbot))
    qtbot.addWidget(dialog)

    assert dialog.radio_precision_low.isChecked() is True
    assert dialog.precision == "low"

    dialog.radio_precision_high.setChecked(True)
    assert dialog.precision == "high"
    assert dialog.radio_precision_low.isChecked() is False  # 互斥


def test_start_recording_blocked_when_content_not_cleared(qtbot, monkeypatch):
    """录制框已有内容时点「录制脚本」（或按 F12）必须弹窗拒绝，不能直接覆盖。"""
    dialog = ScriptRecordDialog(_parent(qtbot))
    qtbot.addWidget(dialog)
    dialog.text_edit.setPlainText("click (0.1, 0.1)")

    warned = []
    monkeypatch.setattr(
        dialog_module.QMessageBox, "warning",
        lambda *args, **kwargs: warned.append((args, kwargs)))

    dialog._start_recording()

    assert len(warned) == 1
    message = warned[0][0][2]
    assert "清除" in message
    assert dialog._recorder is None  # 未真正开始录制

    # toggle_recording（F12 与按钮共用的入口）同样受阻
    dialog.toggle_recording()
    assert len(warned) == 2

    # 避免 qtbot 收尾关闭对话框时触发「未保存」真实弹窗（closeEvent →
    # _confirm_discard → QMessageBox.question，headless 下会直接崩溃）
    dialog._preserved = True


def test_start_recording_not_blocked_when_text_empty(qtbot, monkeypatch):
    """录制框为空时不应弹出「请清除」提示，正常走后续校验。"""
    dialog = ScriptRecordDialog(_parent(qtbot))
    qtbot.addWidget(dialog)
    assert dialog.text_edit.toPlainText() == ""

    warned = []
    monkeypatch.setattr(
        dialog_module.QMessageBox, "warning",
        lambda *args, **kwargs: warned.append((args, kwargs)))

    dialog._start_recording()

    assert warned == []
    # _parent(qtbot) 的 backend 是 "adb"，空文本时应该继续走到这一步的提示
    assert dialog.lbl_status.text() == "ADB 模式暂不支持录制"


def test_high_precision_save_writes_wf_and_central_lvtrace(
    qtbot, monkeypatch, tmp_path,
):
    workflows = tmp_path / "workflows"
    wf_path = workflows / "standalone" / "recorded.wf"

    class _Resolver:
        @staticmethod
        def write_dir(_rel):
            workflows.mkdir(parents=True, exist_ok=True)
            return workflows

    monkeypatch.setattr(dialog_module, "get_resolver", lambda: _Resolver())
    monkeypatch.setattr(
        dialog_module.QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(wf_path), ""),
    )
    dialog = ScriptRecordDialog(_parent(qtbot))
    qtbot.addWidget(dialog)
    dialog._pending_trace = InputTrace(
        1000,
        800,
        (InputTraceEvent(0, "move", (10, -2)),),
    )
    dialog.text_edit.setPlainText(
        'replay input_trace "__LVTRACE__"')

    dialog._on_save()

    text = wf_path.read_text(encoding="utf-8")
    traces = list((workflows / "lvtrace").glob("*.lvtrace"))
    assert len(traces) == 1
    assert f'"../lvtrace/{traces[0].name}"' in text
    assert dialog._preserved is True
    assert dialog.btn_copy.isEnabled() is False
