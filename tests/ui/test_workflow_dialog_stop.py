"""日常、批量与调律共用的工作流弹窗停止语义。"""

from __future__ import annotations

import threading

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QWidget

from lvjiang.ui.main.run_control import _UIHelper


class _Host(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.stop_requests = 0

    def request_stop(self) -> None:
        self.stop_requests += 1


def test_queued_dialog_is_discarded_if_f10_won_the_race(qtbot):
    shown = []
    helper = _UIHelper(stop_check=lambda: True)
    helper._show = lambda *args: shown.append(args)  # type: ignore[method-assign]
    done = threading.Event()
    request = {
        "action": "pause",
        "kwargs": {"message": "不应显示"},
        "result": None,
        "done": done,
    }

    helper._on_request(request)

    assert done.is_set()
    assert shown == []


def test_pause_dialog_has_direct_stop_action(qtbot):
    host = _Host()
    qtbot.addWidget(host)
    helper = _UIHelper(host)

    def click_stop() -> None:
        dialog = helper._active_dialog
        button = next(
            item for item in dialog.buttons() if item.text() == "结束任务"
        )
        button.click()

    QTimer.singleShot(0, click_stop)
    helper._show("pause", {"message": "请处理"})

    assert host.stop_requests == 1


def test_f10_rejects_tuning_choice_with_cancel_value(qtbot):
    host = _Host()
    qtbot.addWidget(host)
    helper = _UIHelper(host)
    QTimer.singleShot(0, helper.close_active_dialog)

    result = helper._show("choose", {
        "message": "材料不足",
        "choices": [
            {"label": "继续调律", "value": "continue", "role": "accept"},
            {"label": "结束本次调律", "value": "end", "role": "reject"},
        ],
        "cancel_value": "end",
    })

    assert result == "end"
