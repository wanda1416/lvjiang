"""工作流只等待业务结果，暂停/确认/输入不能接管主窗口。"""
import threading
from unittest.mock import Mock

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QInputDialog, QMessageBox, QPushButton, QVBoxLayout, QWidget

from lvjiang.ui.main.run_control import RunControlMixin, _UIHelper


class Host(QWidget, RunControlMixin):
    def __init__(self):
        super().__init__()
        self._stop_requested = False
        self._run_state = 'paused'
        self._current_worker = object()
        self.stop_calls = 0
        self.edit_calls = 0
        self.edit = QPushButton('编辑布局', self)
        layout = QVBoxLayout(self)
        layout.addWidget(self.edit)
        self.edit.clicked.connect(self.on_edit)

    def on_edit(self):
        self.edit_calls += 1

    def _is_stopped(self):
        return self._stop_requested

    def request_stop(self):
        self.stop_calls += 1
        self._stop_requested = True


@pytest.mark.parametrize('action,kwargs', [
    ('pause', {'message': '保存情境失败，请手动处理'}),
    ('confirm', {'message': '是否继续？'}),
    ('choose', {'message': '请选择', 'choices': [
        {'label': '继续', 'value': 'continue', 'role': 'accept'},
        {'label': '结束', 'value': 'stop', 'role': 'reject'}]}),
    ('input', {'prompt': '请输入'}),
])
def test_task_waits_while_main_window_and_tools_stay_operable(qtbot, qapp, action, kwargs):
    host = Host()
    qtbot.addWidget(host)
    host.show()
    tool = QPushButton('工具窗口')
    qtbot.addWidget(tool)
    tool.show()
    tool_clicks = []
    tool.clicked.connect(lambda: tool_clicks.append(True))
    callback = host._create_ui_callback()
    returned = threading.Event()
    results = []

    def run():
        results.append(callback(action, **kwargs))
        returned.set()

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        qtbot.waitUntil(lambda: host._ui_helper._active_dialog is not None)
        dialog = host._ui_helper._active_dialog
        assert dialog.isVisible()
        assert dialog.windowModality() == Qt.WindowModality.NonModal
        assert not dialog.isModal() and qapp.activeModalWidget() is None
        if isinstance(dialog, QMessageBox):
            assert dialog.testOption(QMessageBox.Option.DontUseNativeDialog)
        assert host.isVisible() and not host.isMinimized() and host.isEnabled()
        qtbot.mouseClick(host.edit, Qt.MouseButton.LeftButton)
        qtbot.mouseClick(tool, Qt.MouseButton.LeftButton)
        assert host.edit_calls == 1 and tool_clicks == [True]
        assert not returned.is_set()
        if isinstance(dialog, QInputDialog):
            dialog.setTextValue('完成')
            dialog.accept()
        elif action == 'confirm':
            dialog.button(QMessageBox.StandardButton.Yes).click()
        else:
            next(b for b in dialog.buttons() if b.text() == '继续').click()
        qtbot.waitUntil(returned.is_set)
        assert results == [{'pause': None, 'confirm': True, 'choose': 'continue', 'input': '完成'}[action]]
        assert host.isVisible() and host.isEnabled()
    finally:
        host._ui_helper._dismiss_active_dialog()
        worker.join(timeout=1)


def test_pause_stop_button_requests_stop_before_releasing_worker(qtbot):
    host = Host()
    qtbot.addWidget(host)
    host.show()
    helper = _UIHelper(host)
    request = {'action': 'pause', 'kwargs': {'message': '手动处理'}, 'done': threading.Event()}
    helper._on_request(request)
    dialog = helper._active_dialog
    next(b for b in dialog.buttons() if b.text() == '结束任务').click()
    assert host.stop_calls == 1 and request['done'].is_set()
    assert helper._active_dialog is None


def test_f10_dismisses_interaction_and_late_requests_do_not_open(qtbot):
    host = Host()
    qtbot.addWidget(host)
    helper = _UIHelper(host, host._is_stopped)
    request = {'action': 'pause', 'kwargs': {}, 'done': threading.Event()}
    helper._on_request(request)
    host._stop_requested = True
    helper.close_active_dialog()
    qtbot.waitUntil(request['done'].is_set)
    late = {'action': 'pause', 'kwargs': {}, 'done': threading.Event()}
    helper._on_request(late)
    assert late['done'].is_set() and helper._active_dialog is None


@pytest.mark.parametrize('answer', ['yes', 'no', 'close'])
def test_stop_confirmation_is_async_and_nonmodal(qtbot, qapp, answer):
    host = Host()
    qtbot.addWidget(host)
    host.show()
    host._request_stop = Mock()
    # This must return immediately, before any answer is provided.
    RunControlMixin._request_stop(host)
    dialog = host._stop_confirmation_dialog
    assert dialog.isVisible() and not dialog.isModal()
    assert dialog.testOption(QMessageBox.Option.DontUseNativeDialog)
    assert qapp.activeModalWidget() is None
    qtbot.mouseClick(host.edit, Qt.MouseButton.LeftButton)
    assert host.edit_calls == 1 and not host._stop_requested
    RunControlMixin._request_stop(host)
    assert host._stop_confirmation_dialog is dialog
    if answer == 'close':
        dialog.reject()
    else:
        button = QMessageBox.StandardButton.Yes if answer == 'yes' else QMessageBox.StandardButton.No
        dialog.button(button).click()
    assert not host._stop_confirm_pending
    assert host._stop_confirmation_dialog is None
    if answer == 'yes':
        host._request_stop.assert_called_once_with(stop_confirmed=True)
    else:
        host._request_stop.assert_not_called()


def test_background_main_window_is_raised_before_pause_dialog(qtbot, monkeypatch):
    host = Host()
    qtbot.addWidget(host)
    host.show()
    foreground = QWidget()
    qtbot.addWidget(foreground)
    foreground.show()
    foreground.raise_()
    foreground.activateWindow()
    order = []
    original_raise = host.raise_
    monkeypatch.setattr(host, 'raise_', lambda: (order.append('main'), original_raise()))
    original_show = QMessageBox.show

    def show(dialog):
        assert dialog.testOption(QMessageBox.Option.DontUseNativeDialog)
        order.append('dialog')
        original_show(dialog)

    monkeypatch.setattr(QMessageBox, 'show', show)
    helper = _UIHelper(host)
    request = {'action': 'pause', 'kwargs': {}, 'done': threading.Event()}
    helper._on_request(request)
    assert order == ['main', 'dialog']
    assert host.isVisible() and host.isEnabled()
    helper._dismiss_active_dialog()


def test_stale_stop_confirmation_cannot_stop_a_new_task(qtbot):
    host = Host()
    qtbot.addWidget(host)
    host.show()
    host._request_stop = Mock()
    host._confirm_stop_while_paused()
    dialog = host._stop_confirmation_dialog
    host._current_worker = object()
    dialog.button(QMessageBox.StandardButton.Yes).click()
    host._request_stop.assert_not_called()
    assert not host._stop_confirm_pending
