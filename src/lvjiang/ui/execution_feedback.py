"""任务入口的共同展示；运行状态与业务预检仍归宿主和各页面所有。"""
from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QPushButton

from ..i18n import tr
from .button_styles import apply_execution_button_style


class ExecutionFeedback(QLabel):
    """执行按钮附近的可执行性说明与启动错误，不弹出阻塞窗口。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        self._failure = ""
        self._reason = ""
        self.hide()

    def set_reason(self, reason: str) -> None:
        self._reason = reason
        self._render()

    def show_failure(self, message: str) -> None:
        self._failure = message
        self._render()

    def clear_failure(self) -> None:
        self._failure = ""
        self._render()

    def _render(self) -> None:
        self.setText(self._failure or self._reason)
        self.setProperty("status", "danger" if self._failure else "warning")
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)
        self.setVisible(bool(self.text()))


def apply_idle_start(
    button: QPushButton, feedback: ExecutionFeedback | None, host: Any,
    state: str, start_text: str, local_reason: str = "",
) -> None:
    """空闲入口允许点击解释，真正启动仍由原有启动预检和授权门禁决定。"""
    from .main.run_control import STATE_PLAN_UNSUPPORTED, STATE_START_DENIED

    text, reason, variant = start_text, local_reason, "disabled" if local_reason else "run"
    ready = getattr(host, "_backend_ready", None)
    if state == "not_ready" or (state == "idle" and callable(ready) and not ready()):
        target_getter = getattr(host, "_current_execution_target", None)
        target = target_getter() if callable(target_getter) else None
        if target is None:
            text, reason = tr("未连接"), tr("请先定位窗口或连接设备，并选择执行目标")
        elif getattr(host, "_backend", "") == "adb":
            text, reason = tr("未连接"), tr("请先扫描并连接设备")
        else:
            text, reason = tr("未定位"), tr("请先扫描窗口并点击定位")
        variant = "not_ready"
    elif state == STATE_PLAN_UNSUPPORTED:
        text, reason = tr("方案不支持"), tr("当前方案不支持所选后端，请切换方案或连接方式")
        variant = "disabled"
    elif state == STATE_START_DENIED:
        text = host.start_denied_label() or tr("不能启动")
        denial_getter = getattr(host, "_start_denial", None)
        denial = denial_getter() if callable(denial_getter) else None
        reason = denial.reason if denial is not None else text
        variant = "disabled"
    button.setText(text)
    button.setEnabled(True)
    button.setToolTip(reason)
    apply_execution_button_style(button, variant)
    if feedback is not None:
        feedback.set_reason(reason)


def idle_start_state(host: Any) -> str:
    """仅投影宿主既有的连接、方案与授权检查，不维护第二份状态。"""
    from .main.run_control import STATE_PLAN_UNSUPPORTED, STATE_START_DENIED
    if not host._backend_ready():
        return "not_ready"
    if not host._plan_allows_backend():
        return STATE_PLAN_UNSUPPORTED
    return STATE_START_DENIED if host.start_denied_label() else "idle"


def show_running_feedback(feedback: ExecutionFeedback | None, reason: str = "") -> None:
    if feedback is not None:
        feedback.clear_failure()
        feedback.set_reason(tr("当前目标正在执行其他任务，请到对应任务页操作停止或暂停") if reason else "")


def report_start_failure(host: Any, message: str, scope: str = "daily") -> None:
    """按启动入口归属反馈，不把其他页面的失败写到当前查看页。"""
    if scope == "daily":
        feedback = getattr(host, "_daily_start_feedback", None)
        if feedback is not None:
            feedback.show_failure(message)
        return
    tabs = getattr(host, "_left_tabs", None)
    if tabs is not None:
        for index in range(tabs.count()):
            page = tabs.widget(index)
            if getattr(page, "execution_scope", None) == scope:
                page.execution_feedback.show_failure(message)
                return


def clear_start_failures(host: Any) -> None:
    feedback = getattr(host, "_daily_start_feedback", None)
    if feedback is not None:
        feedback.clear_failure()
    tabs = getattr(host, "_left_tabs", None)
    if tabs is not None:
        for index in range(tabs.count()):
            feedback = getattr(tabs.widget(index), "execution_feedback", None)
            if feedback is not None:
                feedback.clear_failure()
