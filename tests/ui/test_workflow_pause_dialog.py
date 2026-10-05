"""工作流暂停对话框只能通过明确操作结束等待。"""

import threading

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget

from lvjiang.ui.main.run_control import _UIHelper


class _Host(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.stop_requests = 0

    def request_stop(self) -> None:
        self.stop_requests += 1


def _show_pause(helper: _UIHelper) -> dict:
    request = {
        "action": "pause",
        "kwargs": {"message": "请手动处理后继续"},
        "result": None,
        "done": threading.Event(),
    }
    helper._on_request(request)
    return request


@pytest.mark.parametrize("focus_target", ["dialog", "continue", "stop"])
def test_escape_does_not_close_pause_dialog(qtbot, focus_target):
    host = _Host()
    qtbot.addWidget(host)
    helper = _UIHelper(host)
    request = _show_pause(helper)
    dialog = helper._active_dialog

    buttons = {button.text(): button for button in dialog.buttons()}
    target = {
        "dialog": dialog,
        "continue": buttons["继续"],
        "stop": buttons["停止任务"],
    }[focus_target]
    target.setFocus()
    qtbot.keyClick(target, Qt.Key.Key_Escape)

    assert dialog.isVisible()
    assert not request["done"].is_set()
    assert host.stop_requests == 0

    # 程序主动停止仍能关闭弹窗并释放工作流线程。
    helper.close_active_dialog()
    qtbot.waitUntil(request["done"].is_set)


def test_stop_button_still_requests_workflow_stop(qtbot):
    host = _Host()
    qtbot.addWidget(host)
    helper = _UIHelper(host)
    request = _show_pause(helper)
    dialog = helper._active_dialog

    stop_button = next(
        button for button in dialog.buttons() if button.text() == "停止任务"
    )
    qtbot.mouseClick(stop_button, Qt.MouseButton.LeftButton)

    qtbot.waitUntil(request["done"].is_set)
    assert host.stop_requests == 1
