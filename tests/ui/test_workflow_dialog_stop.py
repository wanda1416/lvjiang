"""日常、批量与调律共用的工作流弹窗非模态与停止语义。"""

from __future__ import annotations

import threading

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QInputDialog, QMessageBox, QPushButton, QWidget

from lvjiang.ui.main.run_control import RunControlMixin, _UIHelper


class _Host(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.stop_requests = 0
        self.main_clicks = 0
        self.main_button = QPushButton("主界面操作", self)
        self.main_button.clicked.connect(self._record_main_click)

    def _record_main_click(self) -> None:
        self.main_clicks += 1

    def request_stop(self) -> None:
        self.stop_requests += 1

    def _is_stopped(self) -> bool:
        return False


def _request(action: str, **kwargs) -> dict:
    return {
        "action": action,
        "kwargs": kwargs,
        "result": None,
        "done": threading.Event(),
    }


def _assert_main_window_remains_interactive(qtbot, host, dialog) -> None:
    assert dialog.isVisible()
    assert not dialog.isModal()
    assert dialog.windowModality() == Qt.WindowModality.NonModal
    assert host.isEnabled()
    before = host.main_clicks
    qtbot.mouseClick(host.main_button, Qt.MouseButton.LeftButton)
    assert host.main_clicks == before + 1


def test_queued_dialog_is_discarded_if_f10_won_the_race(qtbot):
    shown = []
    helper = _UIHelper(stop_check=lambda: True)
    helper._show_non_modal = lambda *args: shown.append(args)  # type: ignore[method-assign]
    request = _request("pause", message="不应显示")

    helper._on_request(request)

    assert request["done"].is_set()
    assert shown == []


def test_pause_dialog_does_not_block_main_window_and_has_stop_action(qtbot):
    host = _Host()
    qtbot.addWidget(host)
    host.show()
    helper = _UIHelper(host)
    request = _request("pause", message="请处理")

    helper._on_request(request)
    dialog = helper._active_dialog
    _assert_main_window_remains_interactive(qtbot, host, dialog)
    stop = next(item for item in dialog.buttons() if item.text() == "结束任务")
    qtbot.mouseClick(stop, Qt.MouseButton.LeftButton)

    assert request["done"].wait(1)
    assert host.stop_requests == 1


def test_f10_rejects_tuning_choice_with_cancel_value_without_modal_parent(qtbot):
    host = _Host()
    qtbot.addWidget(host)
    host.show()
    helper = _UIHelper(host)
    request = _request(
        "choose",
        message="材料不足",
        choices=[
            {"label": "继续调律", "value": "continue", "role": "accept"},
            {"label": "结束本次调律", "value": "end", "role": "reject"},
        ],
        cancel_value="end",
    )

    helper._on_request(request)
    _assert_main_window_remains_interactive(qtbot, host, helper._active_dialog)
    helper.close_active_dialog()

    assert request["done"].wait(1)
    assert request["result"] == "end"


def test_confirm_and_input_are_non_modal_but_return_workflow_results(qtbot):
    host = _Host()
    qtbot.addWidget(host)
    host.show()
    helper = _UIHelper(host)

    confirm = _request("confirm", message="继续？")
    helper._on_request(confirm)
    box = helper._active_dialog
    assert isinstance(box, QMessageBox)
    _assert_main_window_remains_interactive(qtbot, host, box)
    yes = box.button(QMessageBox.StandardButton.Yes)
    qtbot.mouseClick(yes, Qt.MouseButton.LeftButton)
    assert confirm["done"].wait(1)
    assert confirm["result"] is True

    prompt = _request("input", prompt="输入")
    helper._on_request(prompt)
    dialog = helper._active_dialog
    assert isinstance(dialog, QInputDialog)
    _assert_main_window_remains_interactive(qtbot, host, dialog)
    dialog.setTextValue("结果")
    dialog.accept()
    assert prompt["done"].wait(1)
    assert prompt["result"] == "结果"


def test_worker_waits_for_decision_while_qt_main_window_keeps_working(qtbot):
    host = _Host()
    qtbot.addWidget(host)
    host.show()
    callback = RunControlMixin._create_ui_callback(host)
    result = {}

    worker = threading.Thread(
        target=lambda: result.setdefault(
            "confirmed", callback("confirm", message="继续？")
        )
    )
    worker.start()
    qtbot.waitUntil(lambda: host._ui_helper._active_dialog is not None)
    box = host._ui_helper._active_dialog

    _assert_main_window_remains_interactive(qtbot, host, box)
    assert worker.is_alive()  # 只等待业务决定，不占用 Qt 主线程。
    qtbot.mouseClick(
        box.button(QMessageBox.StandardButton.Yes),
        Qt.MouseButton.LeftButton,
    )
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert result == {"confirmed": True}
