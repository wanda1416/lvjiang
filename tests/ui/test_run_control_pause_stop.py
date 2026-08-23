"""暂停/停止状态机测试

覆盖 2704a9f 引入的"暂停中点结束二次确认"与随之而来的 F8 竞态窗口：
QMessageBox.question 是 Qt 主线程上的嵌套事件循环，但全局热键（pynput
监听线程）直接同步调用 _on_pause_resume，不走 Qt 信号跨线程队列，不受
这个嵌套事件循环阻塞。修复前，弹窗打开期间按 F8 会被误判为"暂停中恢复"
而提前唤醒工作流线程；本文件锁定 _stop_confirm_pending 挡住这个窗口。
"""

from lvjiang.ui.run_control import RunControlMixin


class _PauseEvent:
    """threading.Event 桩：只记录调用，不做真实线程阻塞"""

    def __init__(self):
        self._set = True
        self.set_calls = 0
        self.clear_calls = 0

    def is_set(self):
        return self._set

    def set(self):
        self._set = True
        self.set_calls += 1

    def clear(self):
        self._set = False
        self.clear_calls += 1


class _LogText:
    def append(self, *_a, **_k):
        pass


class _StatusBar:
    def showMessage(self, *_a, **_k):
        pass


class _RunControlStub:
    """RunControlMixin 状态机方法所需的最小属性集"""

    def __init__(self, run_state: str = "paused"):
        self._run_state = run_state
        self._stop_requested = False
        self._pause_event = _PauseEvent()
        self.log_text = _LogText()
        self._resume_calls = 0
        self._pause_calls = 0

    def statusBar(self):
        return _StatusBar()

    def _refresh_pause_button(self):
        pass

    def _refresh_run_button(self):
        pass

    # 覆写以便断言真正的暂停/恢复方法是否被触发
    def _resume_execution(self):
        self._resume_calls += 1
        RunControlMixin._resume_execution(self)

    def _request_pause(self):
        self._pause_calls += 1
        RunControlMixin._request_pause(self)


class TestStopConfirmBlocksF8Race:
    """锁定：确认结束弹窗打开期间，F8 不应提前唤醒工作流线程"""

    def test_f8_during_confirm_dialog_is_ignored(self, monkeypatch):
        from PyQt6.QtWidgets import QMessageBox

        stub = _RunControlStub(run_state="paused")

        def fake_question(*_a, **_k):
            # 模拟弹窗仍开着时，pynput 线程并发按下 F8
            RunControlMixin._on_pause_resume(stub)
            return QMessageBox.StandardButton.Yes

        monkeypatch.setattr(QMessageBox, "question", fake_question)

        confirmed = RunControlMixin._confirm_stop_while_paused(stub)

        assert confirmed is True
        # 关键断言：弹窗期间的 F8 必须被 _stop_confirm_pending 挡住，
        # 不能真的触发 _resume_execution 唤醒线程
        assert stub._resume_calls == 0
        assert stub._run_state == "paused"  # 未被 F8 误改
        # 弹窗关闭后标志位必须复位，否则弹窗后 F8 会永久失效
        assert stub._stop_confirm_pending is False

    def test_f8_after_dialog_closes_works_normally(self, monkeypatch):
        """弹窗关闭（无论是否确认）后，F8 应恢复正常响应"""
        from PyQt6.QtWidgets import QMessageBox

        stub = _RunControlStub(run_state="paused")
        monkeypatch.setattr(
            QMessageBox, "question",
            lambda *a, **k: QMessageBox.StandardButton.No,
        )

        confirmed = RunControlMixin._confirm_stop_while_paused(stub)
        assert confirmed is False
        assert stub._stop_confirm_pending is False

        RunControlMixin._on_pause_resume(stub)
        assert stub._resume_calls == 1
        assert stub._run_state == "running"

    def test_pending_flag_cleared_even_if_question_raises(self, monkeypatch):
        """弹窗回调异常也不能让标志位卡死在 True，否则 F8 永久失效"""
        from PyQt6.QtWidgets import QMessageBox

        stub = _RunControlStub(run_state="paused")

        def boom(*_a, **_k):
            raise RuntimeError("模拟弹窗异常")

        monkeypatch.setattr(QMessageBox, "question", boom)

        try:
            RunControlMixin._confirm_stop_while_paused(stub)
        except RuntimeError:
            pass

        assert stub._stop_confirm_pending is False
