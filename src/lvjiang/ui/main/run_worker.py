"""工作流异步执行线程、暂停交互对话框与运行快照辅助。"""

import threading
from dataclasses import fields, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from loguru import logger
from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QMessageBox

from ...errors import log_failure
from ...i18n import tr


def other_task_running_label(host: Any, scope: str) -> str:
    """只读当前查看目标的运行快照，非所属页面仅展示占用状态。"""
    context = getattr(host, "_current_run_context", None)
    if context is None:
        from ...core.control import get_active_control_server

        srv = get_active_control_server()
        if srv is not None and srv.has_active_job():
            return tr("任务运行中")
        return ""
    execution_scope = context.metadata.get("execution_scope", "daily")
    if execution_scope == scope:
        return ""
    if execution_scope == "daily":
        return tr("日常任务运行中")
    if execution_scope == "batch":
        return tr("批量任务运行中")
    # 代码工作流的执行范围即其注册名；专属文案由工作流类自己声明，
    # 宿主不认识任何具体工作流。
    from ...workflows.implementations import workflow_running_label
    declared = workflow_running_label(str(execution_scope))
    return tr(declared) if declared else tr("任务运行中")


class _AcknowledgedPauseEvent(threading.Event):
    """首次被工作线程观察为 clear 时通知 UI 已到达暂停临界点。"""

    def __init__(self, acknowledged: Callable[[], None]):
        super().__init__()
        self._acknowledged = acknowledged
        self._ack_lock = threading.Lock()
        self._ack_sent = False

    def clear(self) -> None:
        with self._ack_lock:
            self._ack_sent = False
        super().clear()

    def _ack_if_paused(self) -> None:
        if super().is_set():
            return
        with self._ack_lock:
            if self._ack_sent:
                return
            self._ack_sent = True
        self._acknowledged()

    def is_set(self) -> bool:
        value = super().is_set()
        if not value:
            self._ack_if_paused()
        return value

    def wait(self, timeout: float | None = None) -> bool:
        self._ack_if_paused()
        return super().wait(timeout)

class _StreamRecheckBridge(QObject):
    """空闲重建画面流的工作线程 → 主线程结果回调。

    ``AndroidStreamCapture.restart()`` 会阻塞（adb push / forward / 等协议头与
    首帧），必须离开 UI 线程执行；结果经信号回到主线程再碰控件。
    """
    finished = pyqtSignal(str, bool, str)   # target_id, ok, 失败原因

    def __init__(self, target_id: str) -> None:
        super().__init__()
        self.target_id = target_id


def _to_serializable(obj):
    """将包含 to_dict() 对象的列表/字典转为可 JSON 序列化的结构"""
    if isinstance(obj, list):
        return [_to_serializable(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _to_serializable(v) for k, v in obj.items()}
    if hasattr(obj, 'to_dict'):
        return obj.to_dict()
    return obj


def _to_history_snapshot(obj):
    """无深拷贝地把专用任务运行上下文转成稳定输入快照。"""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (list, tuple, set)):
        return [_to_history_snapshot(item) for item in obj]
    if isinstance(obj, dict):
        return {str(key): _to_history_snapshot(value)
                for key, value in obj.items()}
    if is_dataclass(obj) and not isinstance(obj, type):
        return {
            field.name: _to_history_snapshot(getattr(obj, field.name))
            for field in fields(obj)
        }
    if hasattr(obj, "to_dict"):
        try:
            return _to_history_snapshot(obj.to_dict())
        except Exception:  # noqa: BLE001
            pass
    return str(obj)


class WorkflowWorker(QThread):
    """工作流异步执行线程"""

    def __init__(self, flow_id: str, fn, parent=None, *, task_run=None):
        super().__init__(parent)
        self.flow_id = flow_id
        self._fn = fn
        self.task_run = task_run
        self.result_or_exception: Any = None

    def run(self):
        if self.task_run is not None:
            with self.task_run.capture_logs():
                logger.info(
                    f"任务开始: task_run_id={self.task_run.task_run_id}, "
                    f"task_id={self.flow_id}")
                self._execute()
                logger.info(
                    f"任务线程结束: task_run_id={self.task_run.task_run_id}")
        else:
            self._execute()

    def _execute(self):
        try:
            self.result_or_exception = self._fn()
        except BaseException as e:
            log_failure(f"工作流 {self.flow_id} 异常退出", e)
            self.result_or_exception = e


def _prepare_modeless_dialog(dialog) -> None:
    """任务交互使用 Qt 窗口，避免原生消息框接管应用的模态/窗口行为。"""
    from PyQt6.QtWidgets import QMessageBox

    if isinstance(dialog, QMessageBox):
        dialog.setOption(QMessageBox.Option.DontUseNativeDialog, True)
    dialog.setModal(False)
    dialog.setWindowModality(Qt.WindowModality.NonModal)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)


def _show_modeless_dialog(dialog) -> None:
    """先抬起可见宿主，再显示提示；Windows 后台宿主不能只留下子弹窗。"""
    owner = dialog.parentWidget()
    if owner is not None:
        owner = owner.window()
        if owner.isVisible() and not owner.isMinimized():
            owner.raise_()
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()


class _PauseMessageBox(QMessageBox):
    """暂停提示只允许用户通过明确按钮决定继续或结束。"""

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() == Qt.Key.Key_Escape:
            event.accept()
            return
        super().keyPressEvent(event)


class _UIHelper(QObject):
    """工作流线程 → 主线程的非模态对话框桥。

    请求以 dict 携带（信号用 object 签名，避免 QVariant 拷贝、保持引用）：
    主线程展示非模态对话框；用户完成交互后写 req["result"] 并 set
    req["done"]，工作流线程可以等待业务结果，但 Qt 主窗口始终可操作。
    槽是 QObject 方法，AutoConnection 跨线程投递行为确定为 Queued。
    """
    request = pyqtSignal(object)
    dismiss_active = pyqtSignal()

    def __init__(
        self,
        window=None,
        stop_check: Callable[[], bool] | None = None,
        task_stop_callback: Callable[[], None] | None = None,
    ):
        super().__init__()
        self._window = window
        self._stop_check = stop_check or (lambda: False)
        # 批量运行：弹窗「停止任务」只结束当前任务；None 表示单任务，走整跑停止。
        self._task_stop_callback = task_stop_callback
        self._active_dialog: Any = None
        self.request.connect(self._on_request)
        self.dismiss_active.connect(self._dismiss_active_dialog)

    def _on_request(self, req: dict):
        """主线程：展示非模态交互；完成前只阻塞工作流线程。"""
        try:
            # F10 可能先于 queued request 抵达主线程；此时直接释放工作线程，
            # 不能在停止请求之后再打开一个新的阻塞弹窗。
            if self._stop_check():
                self._complete(req, None)
                return
            self._show_non_modal(req)
        except Exception as e:
            logger.error(f"UI 交互对话框异常: {e}")
            self._complete(req, None)

    def _complete(self, req: dict, result: Any) -> None:
        """Exactly-once completion shared by buttons, title-bar close and F10."""
        if req.get("_completed"):
            return
        req["_completed"] = True
        req["result"] = result
        req["done"].set()

    def _install_dialog(
        self,
        req: dict,
        dialog: Any,
        resolve: Callable[[int], Any],
    ) -> None:
        """Show one task dialog without disabling its parent window."""
        _prepare_modeless_dialog(dialog)
        self._active_dialog = dialog

        def finished(code: int) -> None:
            if self._active_dialog is dialog:
                self._active_dialog = None
            try:
                result = resolve(code)
            except Exception as exc:
                logger.error(f"工作流交互结果处理失败: {exc}")
                result = None
            self._complete(req, result)

        dialog.finished.connect(finished)
        _show_modeless_dialog(dialog)

    def _show_non_modal(self, req: dict) -> None:
        action = req["action"]
        kwargs = req["kwargs"]
        from PyQt6.QtWidgets import (
            QAbstractButton,
            QDialog,
            QInputDialog,
            QMessageBox,
            QPushButton,
        )
        if action == "confirm":
            box = QMessageBox(
                QMessageBox.Icon.Question, tr("工作流确认"),
                kwargs.get("message", ""),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                self._window,
            )
            def resolve_confirm(_code: int) -> bool:
                clicked = box.clickedButton()
                return (
                    clicked is not None and
                    box.standardButton(clicked) == QMessageBox.StandardButton.Yes
                )

            self._install_dialog(
                req,
                box,
                resolve_confirm,
            )
            return
        if action == "choose":
            # 通用多选一对话框；业务文案和值全部由调用方提供。
            box = QMessageBox(self._window)
            box.setIcon(QMessageBox.Icon.Question)
            box.setWindowTitle(tr("工作流确认"))
            box.setText(kwargs.get("message", ""))
            roles = {
                "accept": QMessageBox.ButtonRole.AcceptRole,
                "destructive": QMessageBox.ButtonRole.DestructiveRole,
                "reject": QMessageBox.ButtonRole.RejectRole,
            }
            buttons: dict[QAbstractButton, object] = {}
            default_button: QPushButton | None = None
            choices = kwargs.get("choices") or []
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                button = box.addButton(
                    str(choice.get("label", "")),
                    roles.get(str(choice.get("role", "reject")),
                              QMessageBox.ButtonRole.RejectRole),
                )
                if button is None:
                    continue
                buttons[button] = choice.get("value")
                if default_button is None:
                    default_button = button
            if default_button is not None:
                box.setDefaultButton(default_button)
            self._install_dialog(
                req,
                box,
                lambda _code: (
                    buttons.get(clicked, kwargs.get("cancel_value"))
                    if (clicked := box.clickedButton()) is not None
                    else kwargs.get("cancel_value")
                ),
            )
            return
        if action == "pause":
            box = _PauseMessageBox(self._window)
            box.setIcon(QMessageBox.Icon.Information)
            box.setWindowTitle(tr("工作流暂停"))
            box.setText(kwargs.get("message", ""))
            continue_button = box.addButton(
                tr("继续"), QMessageBox.ButtonRole.AcceptRole
            )
            stop_button = box.addButton(
                tr("停止任务"), QMessageBox.ButtonRole.RejectRole
            )
            if continue_button is not None:
                box.setDefaultButton(continue_button)
            def resolve_pause(_code: int) -> None:
                if stop_button is not None and box.clickedButton() is stop_button:
                    if self._task_stop_callback is not None:
                        # 批量：只结束当前这一条任务，批次继续下一项。
                        self._task_stop_callback()
                    else:
                        request_stop = getattr(self._window, "request_stop", None)
                        if callable(request_stop):
                            request_stop()
                return None

            self._install_dialog(req, box, resolve_pause)
            return
        if action == "input":
            dlg = QInputDialog(self._window)
            dlg.setWindowTitle(tr("工作流输入"))
            dlg.setLabelText(kwargs.get("prompt", ""))
            self._install_dialog(
                req,
                dlg,
                lambda code: dlg.textValue()
                if code == QDialog.DialogCode.Accepted else None,
            )
            return
        if action == "notify":
            # DSL notify: 写入告警面板（弹窗已在 builtin 层完成）
            message = kwargs.get("message", "")
            now = datetime.now()
            alert_id = f"dsl:notify:{now.strftime('%Y%m%d%H%M%S%f')}"
            # push_alert 内部调用 add_alert（含去重），同时更新 UI
            if self._window and getattr(self._window, 'alert_panel', None) is not None:
                self._window.alert_panel.push_alert(alert_id, message, now.isoformat())
            self._complete(req, None)
            return
        if action == "app_event":
            from ...core.app_events import AppEvent
            event = kwargs.get("event")
            if self._window is not None and isinstance(event, AppEvent):
                self._window.app_event.emit(event)
            self._complete(req, None)
            return
        logger.warning(f"未知 UI 交互类型: {action}")
        self._complete(req, None)

    def close_active_dialog(self):
        """关闭当前活动对话框（可安全地从全局热键线程调用）。

        confirm 返回 false、input 返回 null、pause 立即返回，
        使阻塞在对话框上的工作流能响应停止请求。
        """
        self.dismiss_active.emit()

    def _dismiss_active_dialog(self) -> None:
        """Helper 所在线程执行真正的 Qt 窗口操作。"""
        if self._active_dialog is not None:
            self._active_dialog.reject()


