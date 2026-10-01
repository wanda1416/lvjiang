"""场景管理的脚本测试器：不冻 UI、可以结束、点不坏

原实现在 UI 线程里同步跑引擎。引擎里全是 OCR、等待和长按，于是编辑器和主界面一起
冻住——卡死期间连"结束运行"都点不到，而且压根没有结束机制。

这里钉住状态机：按钮在「运行脚本 / 结束运行」之间切换、请求停止后立即禁用避免连点、
线程结束无论什么原因都要把按钮放回去、脚本还在跑时不许关窗口（线程的 parent 就是
这个对话框，而它带 WA_DeleteOnClose）。
"""

import pytest
from PyQt6.QtWidgets import QPushButton, QTextEdit, QWidget

from lvjiang.ui.scene_editor.script_ops import ScriptOpsMixin

pytestmark = pytest.mark.usefixtures('qapp')


class _StatusBar:
    def __init__(self):
        self.message = ""

    def showMessage(self, message):  # noqa: N802 - Qt API shape
        self.message = message


class _Worker:
    """假工作线程：只回答 isRunning，并带一个可设置的结果"""

    def __init__(self, running: bool = True, outcome=None):
        self._running = running
        self.result_or_exception = outcome

    def isRunning(self) -> bool:  # noqa: N802 - Qt API shape
        return self._running


class _Host(QWidget, ScriptOpsMixin):
    def __init__(self):
        super().__init__()
        self._btn_run_script = QPushButton("运行脚本", self)
        self._result_text = QTextEdit(self)
        self._script_text = QTextEdit(self)
        self._status_bar = _StatusBar()
        self._script_worker = None
        self._script_stop_requested = False


def _host(qtbot) -> _Host:
    host = _Host()
    qtbot.addWidget(host)
    return host


class TestToggle:
    def test_running_ui_switches_to_stop(self, qtbot):
        host = _host(qtbot)
        host._set_script_running_ui(True)
        assert host._btn_run_script.text() == "结束运行"
        assert host._btn_run_script.isEnabled()

        host._set_script_running_ui(False)
        assert host._btn_run_script.text() == "运行脚本"

    def test_click_while_running_requests_stop(self, qtbot):
        host = _host(qtbot)
        host._script_worker = _Worker(running=True)
        host._set_script_running_ui(True)

        host._on_script_test()

        assert host._script_stop_requested is True
        # 停止是异步的：按钮立刻禁用，免得重复点击看起来没响应
        assert not host._btn_run_script.isEnabled()
        assert host._btn_run_script.text() == "正在结束..."

    def test_repeat_clicks_while_stopping_change_nothing(self, qtbot):
        host = _host(qtbot)
        host._script_worker = _Worker(running=True)
        host._on_script_test()
        host._on_script_test()
        host._on_script_test()
        assert host._script_stop_requested is True
        assert not host._btn_run_script.isEnabled()


class TestFinish:
    def test_finish_restores_button(self, qtbot):
        host = _host(qtbot)
        host._script_worker = _Worker(running=False, outcome=(None, None))
        host._set_script_running_ui(True)

        host._on_script_test_finished()

        assert host._btn_run_script.text() == "运行脚本"
        assert host._btn_run_script.isEnabled()
        assert host._script_worker is None

    def test_finish_after_stop_says_so(self, qtbot):
        host = _host(qtbot)
        host._script_worker = _Worker(running=False, outcome=(None, None))
        host._script_stop_requested = True

        host._on_script_test_finished()

        assert "结束" in host._status_bar.message
        # 标志必须清掉：否则下一轮会一启动就自杀
        assert host._script_stop_requested is False

    def test_exception_is_shown_and_button_restored(self, qtbot):
        host = _host(qtbot)
        host._script_worker = _Worker(
            running=False, outcome=RuntimeError("炸了"))
        host._set_script_running_ui(True)

        host._on_script_test_finished()

        assert "炸了" in host._result_text.toPlainText()
        assert host._btn_run_script.text() == "运行脚本"

    def test_result_is_rendered(self, qtbot):
        host = _host(qtbot)
        host._script_worker = _Worker(
            running=False, outcome=({"a": 1}, "ok"))
        host._on_script_test_finished()
        text = host._result_text.toPlainText()
        assert "ok" in text and '"a"' in text


class TestCloseGuard:
    def test_close_is_vetoed_while_running(self, qtbot, monkeypatch):
        from lvjiang.ui.scene_editor import script_ops
        shown: list = []
        monkeypatch.setattr(
            script_ops.QMessageBox, "information",
            lambda *a, **k: shown.append(a))

        host = _host(qtbot)
        host._script_worker = _Worker(running=True)
        assert host._confirm_script_stopped_before_close() is False
        assert host._script_stop_requested is True   # 顺带请求了停止
        assert shown, "应当提示脚本仍在运行"

    def test_close_allowed_when_idle(self, qtbot):
        host = _host(qtbot)
        assert host._confirm_script_stopped_before_close() is True
