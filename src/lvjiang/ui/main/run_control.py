"""运行控制混入类 - 用户/布局选择器、启停控制、工作流通用执行"""

import copy
import json
import threading
import traceback
from dataclasses import fields, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from loguru import logger
from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QMessageBox

from lvjiang.apps import get_registry

from ...core.config.resolver import get_resolver
from ...core.fs_util import dated_output_dir
from ...i18n import tr
from ...workflows.engine import DeviceWorkflowEngineBuilder, WorkflowEngine
from ..button_styles import apply_execution_button_style
from .execution_access import guarded_finish, guarded_launch

# 顶部上下文选择器的锁定原因。定义在这里而不是 window.py：window 已经
# import 本模块，反向 import 会成环。
LOCK_REASON_BATCH = "batch"
LOCK_REASON_PLAN = "plan"
LOCK_REASON_RUNNING = "running"

# 方案下拉的「不使用方案」项，userData 为空串。
PLAN_CUSTOM_LABEL = tr("- 自定义 -")

# automation_state_changed 的非常规状态。订阅方必须显式处理——
# 它们的 else 分支都会把未知状态当成「就绪」。
STATE_PLAN_UNSUPPORTED = "plan_unsupported"
#: 目标空闲但并发门禁不允许再起一个任务（当前仅 Lv1 未激活会命中）
STATE_START_DENIED = "start_denied"
STATE_PAUSING = "pausing"
STATE_STOPPING = "stopping"


def other_task_running_label(host: Any, scope: str) -> str:
    """只读当前查看目标的运行快照，非所属页面仅展示占用状态。"""
    context = getattr(host, "_current_run_context", None)
    if context is None or context.metadata.get("execution_scope", "daily") == scope:
        return ""
    return tr("{name}运行中").format(name=context.name)


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


def _log_workflow_result(flow_id: str, result: Any, *, interrupted: bool) -> bool:
    """Log a workflow's structured result unless it has a dedicated report.

    A plugin may already write its own detailed report and history.
    """
    if flow_id in get_registry().get("result_log_suppressed_ids", ()):
        return False
    serializable = _to_serializable(result)
    tag = tr("（用户中断，部分结果）") if interrupted else ""
    logger.info(
        f"工作流 {flow_id} 结果{tag}: "
        f"{json.dumps(serializable, ensure_ascii=False, indent=2)}"
    )
    return True


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
            tb = traceback.format_exc()
            logger.error(f"工作流 {self.flow_id} 异常退出:\n{tb}")
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
    ):
        super().__init__()
        self._window = window
        self._stop_check = stop_check or (lambda: False)
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
            from ..app_events import AppEvent
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


class RunControlMixin:
    """运行控制混入类

    依赖主类提供:
        _user_manager, _session_manager, _layout_manager, _target_window,
        _running, _stop_requested, _capture, _ocr, _input, _overlay,
        user_combo, layout_combo, workflow_combo, btn_run_workflow,
        _param_panel, log_text, statusBar()
    """

    # 运行态属性的类级兜底：实例赋值前直接访问也有明确默认值
    _current_engine = None      # type: ignore[assignment]  # 运行中的 WorkflowEngine（_execute_workflow 赋值）
    _ui_helper = None           # 工作流交互对话框 helper（运行期注入）
    _param_panel = None         # 参数面板（MainWindow._setup_ui 构建）
    _left_tabs = None           # 左侧页签（MainWindow._setup_ui 构建）
    _batch_tab = None           # 批量执行 Tab（MainWindow._build_left_tabs 构建）
    _run_state = "idle"         # idle / running / pausing / paused / stopping
    _pause_event: threading.Event | None = None  # 暂停事件：set=运行，clear=暂停阻塞
    _stop_confirm_pending = False  # 异步停止确认期间防止重复请求及暂停热键抢跑
    _stop_confirmation_dialog: Any = None
    # 进入方案前暂存的自定义组合 (图库, 环境 key, 布局)；切回自定义时还原
    _custom_context: tuple[str, Any, str] | None = None

    @property
    def _running(self) -> bool:
        """当前查看目标是否有运行实例。"""
        manager = getattr(self, "_run_manager", None)
        registry = getattr(self, "_execution_targets", None)
        target_id = registry.active_target_id if registry is not None else None
        if manager is not None and target_id:
            return manager.run_for_target(target_id) is not None
        return getattr(self, '_run_state', 'idle') != 'idle'

    def _project_run_context_for_target(self, target_id: str | None) -> None:
        """把目标对应的运行实例投影到迁移期单任务 UI 字段。"""
        run_context = (
            self._run_manager.run_for_target(target_id) if target_id else None
        )
        self._current_run_context = run_context
        if run_context is None:
            self._current_worker = None
            self._current_engine = None
            self._execution_lease = None
            self._ui_helper = None
            self._pause_event = None
            self._stop_requested = False
            self._run_state = "idle"
            self._running_target_id = None
            self._running_target_snapshot = None
            return
        self._current_worker = run_context.worker
        self._current_engine = run_context.engine
        self._execution_lease = run_context.lease
        self._ui_helper = run_context.ui_helper
        self._pause_event = run_context.pause_event
        self._stop_requested = run_context.stop_event.is_set()
        self._run_state = run_context.state.value
        self._running_target_id = run_context.target_id
        self._running_target_snapshot = run_context.target_snapshot

    def _capture_launch_draft(self, target_id: str | None) -> None:
        target = self._execution_targets.get(target_id)
        if target is None or self._running:
            return
        from .execution_targets import LaunchDraft
        selector = getattr(self, "_daily_execution_user_selector", None)
        username = selector.combo.currentData() if selector is not None else None
        flow_cfg = self._get_selected_flow_config()
        parameters: dict[str, Any] = {}
        if flow_cfg and flow_cfg.get("scope", "daily") == "daily":
            parameters = self._collect_displayed_params(
                flow_cfg.get("parameters", []))
        target.launch_draft = LaunchDraft(
            username=str(username) if username is not None else None,
            workflow_id=str(self.workflow_combo.currentData() or ""),
            environment=str(self._env_combo.currentData() or ""),
            layout=str(self.layout_combo.currentData() or ""),
            plan_id=str(self.plan_combo.currentData() or ""),
            reference_space=self.reference_space_combo.currentText(),
            parameters=parameters,
        )

    @staticmethod
    def _set_combo_data(combo, value) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _restore_launch_draft(self, target_id: str, draft_override=None) -> None:
        target = self._execution_targets.get(target_id)
        draft = draft_override or (target.launch_draft if target is not None else None)
        if draft is None:
            return
        selector = getattr(self, "_daily_execution_user_selector", None)
        controls = (
            self.plan_combo, self.reference_space_combo, self._env_combo,
            self.layout_combo, self.workflow_combo,
        )
        for control in controls:
            control.blockSignals(True)
        if selector is not None:
            selector.combo.blockSignals(True)
        try:
            self._set_combo_data(self.plan_combo, draft.plan_id)
            if draft.reference_space:
                index = self.reference_space_combo.findText(draft.reference_space)
                if index >= 0:
                    self.reference_space_combo.setCurrentIndex(index)
            self._set_combo_data(self._env_combo, draft.environment)
            self._set_combo_data(self.layout_combo, draft.layout)
            self._set_combo_data(self.workflow_combo, draft.workflow_id)
            if selector is not None:
                self._set_combo_data(selector.combo, draft.username)
        finally:
            for control in controls:
                control.blockSignals(False)
            if selector is not None:
                selector.combo.blockSignals(False)
        flow_cfg = self._get_selected_flow_config()
        self._displayed_script_id = flow_cfg["id"] if flow_cfg else None
        for name in ("reference_space_combo", "_env_combo", "layout_combo"):
            setter = getattr(getattr(self, name, None), "set_locked", None)
            if setter is not None:
                setter(LOCK_REASON_PLAN, bool(draft.plan_id))
        self._rebuild_param_panel()
        self._apply_launch_draft_parameters(draft.parameters)

    def _apply_launch_draft_parameters(self, values: dict[str, Any]) -> None:
        if not values:
            return
        from PyQt6.QtWidgets import (
            QCheckBox,
            QComboBox,
            QLineEdit,
            QPlainTextEdit,
            QSpinBox,
            QWidget,
        )
        for name, value in values.items():
            widget = self._param_panel.findChild(QSpinBox, name)
            if widget is not None:
                widget.blockSignals(True)
                widget.setValue(int(value))
                widget.blockSignals(False)
                continue
            widget = self._param_panel.findChild(QCheckBox, name)
            if widget is not None:
                widget.blockSignals(True)
                widget.setChecked(bool(value))
                widget.blockSignals(False)
                continue
            combo = self._param_panel.findChild(QComboBox, name)
            if combo is not None:
                combo.blockSignals(True)
                index = combo.findData(value)
                if index < 0:
                    index = combo.findText(str(value))
                if index >= 0:
                    combo.setCurrentIndex(index)
                combo.blockSignals(False)
                continue
            line = self._param_panel.findChild(QLineEdit, name)
            if line is not None:
                line.blockSignals(True)
                line.setText(str(value))
                line.blockSignals(False)
                continue
            plain = self._param_panel.findChild(QPlainTextEdit, name)
            if plain is not None:
                plain.blockSignals(True)
                plain.setPlainText(str(value))
                plain.blockSignals(False)
                continue
            container = self._param_panel.findChild(QWidget, name)
            if container is not None and isinstance(value, dict):
                for check in container.findChildren(QCheckBox):
                    check.blockSignals(True)
                    check.setChecked(bool(value.get(check.objectName(), False)))
                    check.blockSignals(False)
        self._refresh_param_visibility()

    # ─── 工作流配置加载 ──────────────────────────────────

    def _selected_run_env(self) -> str:
        """Return the environment currently held by the main-window UI."""
        combo = self._env_combo
        value = combo.currentData()
        return str(value) if value is not None else ""

    def _load_workflow_configs(self):
        """发现全部脚本，按作者声明与用户偏好过滤排序后填充下拉。

        脚本本体（.wf + 内置类）由发现层自动扫描；暴露层只决定日常页
        暴露哪些脚本、顺序、以及可选的显示名覆盖。暴露层逻辑与设备端
        悬浮面板共用 ``list_exposed_scripts()``。
        """
        from ...workflows.discovery import (
            last_discovery_problems,
            list_exposed_scripts,
            script_display_name,
        )

        # 环境切换只会改变“不支持”提示，不应把用户选中的日常任务重置
        # 为第一项。清空 combo 前先按稳定 id 留住当前选择。
        selected_workflow_id = self.workflow_combo.currentData()
        if selected_workflow_id is None:
            selected_workflow_id = getattr(self, "_displayed_script_id", None)
        self._workflow_configs: list[dict] = []
        self._loaded_flow_index: int | None = None   # 临时加载的外部工作流在列表中的位置

        current_env = self._selected_run_env()
        try:
            # env 是脚本级启动契约：不匹配的脚本不是“可选但
            # 不能跑”，而是根本不进入当前环境的候选集。
            self._workflow_configs = list_exposed_scripts(current_env)
        except Exception as e:
            logger.error(f"发现脚本失败: {e}")
            # 发现结果已经不可用，上一次成功刷新留下的告警同样不能继续
            # 冒充当前状态。
            self._show_ignored_scripts([])
            return
        self._show_ignored_scripts(last_discovery_problems())

        # 填充下拉列表（block 信号，避免 addItem 逐条触发 _on_workflow_combo_changed）
        self.workflow_combo.blockSignals(True)
        self.workflow_combo.clear()
        for cfg in self._workflow_configs:
            full_display_name = script_display_name(cfg)
            # 放完整名字：窄的时候 Qt 自己按可用宽度省略（CE_ComboBoxLabel
            # 会 elide），分栏拉宽后就能完整显示。预先截断成定长会让「拉宽」
            # 永远看不到更多内容。
            self.workflow_combo.addItem(full_display_name, cfg["id"])
            item_index = self.workflow_combo.count() - 1
            self.workflow_combo.setItemData(
                item_index,
                full_display_name,
                Qt.ItemDataRole.ToolTipRole,
            )
        selected_index = self.workflow_combo.findData(selected_workflow_id)
        if selected_index >= 0:
            self.workflow_combo.setCurrentIndex(selected_index)
        self.workflow_combo.blockSignals(False)

        # 初始化当前面板显示的脚本追踪（供日常配置持久化使用）
        current_cfg = self._get_selected_flow_config()
        self._displayed_script_id = current_cfg["id"] if current_cfg else None

        # 批量页的脚本候选同源于 list_exposed_scripts()，必须一起刷新，
        # 否则它会一直停留在启动时的快照（见 BatchTab.refresh_scripts）。
        if self._batch_tab is not None:
            self._batch_tab.refresh_scripts()

        logger.info(f"已加载 {len(self._workflow_configs)} 个脚本配置")

    def _show_ignored_scripts(self, problems) -> None:
        """把发现层忽略的 .wf 及原因显示在脚本下拉框下方。"""
        label = getattr(self, "_ignored_scripts_warning", None)
        if label is None:
            return
        if not problems:
            label.setVisible(False)
            label.setText("")
            return
        lines = [
            tr("[警告] 以下 {n} 个脚本文件存在问题，请在脚本编辑器中修正：")
            .format(n=len(problems))
        ]
        lines += [f"• {item.wf_file}：{item.message}" for item in problems]
        label.setText("\n".join(lines))
        label.setVisible(True)

    def _on_load_workflow(self):
        """加载任意 .wf 文件为临时工作流项（非常驻，打开新文件会覆盖）

        名字/参数/可选项从 .wf 文件顶部的 `#%` front-matter 元数据提取。
        """
        from PyQt6.QtWidgets import QFileDialog

        from ...workflows.metadata import build_flow_config
        path, _ = QFileDialog.getOpenFileName(
            self, tr("加载工作流文件"), str(get_resolver().write_dir("workflows")),
            tr("工作流文件 (*.wf);;所有文件 (*)"),
        )
        if not path:
            return
        p = Path(path)
        cfg = build_flow_config(p)
        # 覆盖上一次加载的临时项，否则追加
        if self._loaded_flow_index is not None and self._loaded_flow_index < len(self._workflow_configs):
            idx = self._loaded_flow_index
            self._workflow_configs[idx] = cfg
            self.workflow_combo.setItemText(idx, cfg["name"])
            self.workflow_combo.setItemData(idx, cfg["id"])
            self.workflow_combo.setItemData(
                idx, cfg["name"], Qt.ItemDataRole.ToolTipRole)
        else:
            self._workflow_configs.append(cfg)
            self.workflow_combo.addItem(
                cfg["name"], cfg["id"])
            self._loaded_flow_index = len(self._workflow_configs) - 1
            self.workflow_combo.setItemData(
                self._loaded_flow_index,
                cfg["name"],
                Qt.ItemDataRole.ToolTipRole,
            )
        self.workflow_combo.setCurrentIndex(self._loaded_flow_index)
        self.log_text.append(f"[加载] 已加载工作流: {cfg['name']}")

    def _get_selected_flow_config(self) -> dict | None:
        """获取当前选中的工作流配置"""
        idx = self.workflow_combo.currentIndex()
        if idx < 0 or idx >= len(self._workflow_configs):
            return None
        return self._workflow_configs[idx]

    def _collect_flow_params(self) -> dict:
        """从参数面板收集当前工作流的参数值"""
        flow_cfg = self._get_selected_flow_config()
        if not flow_cfg:
            return {}
        params: dict[str, Any] = {}
        panel = self._param_panel
        if panel is None:
            return params
        from PyQt6.QtWidgets import (
            QCheckBox,
            QComboBox,
            QLineEdit,
            QPlainTextEdit,
            QSpinBox,
            QWidget,
        )
        for param_def in flow_cfg.get("parameters", []):
            name = param_def["name"]
            # checkgroup：从容器内收集各复选框状态为 dict
            if param_def.get("type") == "checkgroup":
                container = panel.findChild(QWidget, name)
                if container is not None:
                    group = {}
                    for chk in container.findChildren(QCheckBox):
                        group[chk.objectName()] = chk.isChecked()
                    params[name] = group
                continue
            # 先找 QSpinBox
            widget = panel.findChild(QSpinBox, name)
            if widget is not None:
                params[name] = str(widget.value())
                continue
            # 再找 QCheckBox（bool 参数，传 True/False）
            widget = panel.findChild(QCheckBox, name)
            if widget is not None:
                params[name] = widget.isChecked()
                continue
            # 再找 QComboBox
            widget = panel.findChild(QComboBox, name)
            if widget is not None:
                data = widget.currentData()
                params[name] = data if data is not None else widget.currentText()
                continue
            # 最后找 QLineEdit（text 参数，原样传字符串）
            widget = panel.findChild(QLineEdit, name)
            if widget is not None:
                params[name] = widget.text()
                continue
            multiline = panel.findChild(QPlainTextEdit, name)
            if multiline is not None:
                params[name] = multiline.toPlainText()
        return params

    # ─── 用户选择器 ────────────────────────────────────────

    def _refresh_user_combo(self):
        """刷新用户选择器下拉列表"""
        self.user_combo.blockSignals(True)
        self.user_combo.clear()
        users = self._user_manager.list_users()
        active = self._user_manager.get_active_user_name()
        self.user_combo.addItems(users)
        idx = self.user_combo.findText(active)
        if idx >= 0:
            self.user_combo.setCurrentIndex(idx)
        self.user_combo.blockSignals(False)
        # 日常/调律的执行用户选择是纯运行期状态；用户增删后刷新候选，
        # 仍保留有效的固定选择，不参与 session/config 持久化。
        from ..execution_user_selector import ExecutionUserSelector
        for selector in self.findChildren(ExecutionUserSelector):
            selector.refresh_users()
        if hasattr(self, "_daily_execution_user_selector"):
            self._on_daily_execution_user_changed(
                self._daily_execution_user_selector.resolve_username()
            )

    def _on_user_changed(self, index: int):
        """用户选择器切换"""
        if index < 0:
            return
        name = self.user_combo.currentText()
        old_name = self._user_manager.get_active_user_name()
        if name and name != old_name:
            # 切换只影响 UI 展示；正在运行的任务已在启动时绑定用户名，
            # 其 session 落盘归属不受此处切换影响
            self._user_manager.set_active_user(name)
            logger.info(f"已切换到用户: {name}")
            # “跟随当前用户”的执行选择器不会发生下拉索引变化，因此不会
            # 自己发出 resolved_user_changed；这里显式切换参数展示上下文。
            if hasattr(self, "_daily_execution_user_selector"):
                self._on_daily_execution_user_changed(
                    self._daily_execution_user_selector.resolve_username()
                )
        # 通知插件页面刷新其用户相关状态。
        self.user_changed.emit(self._user_manager.get_active_user_name() or "")

    # ─── 图库空间选择器 ────────────────────────────────────

    def _refresh_reference_space_combo(self):
        """重扫图库空间，并让主页面下拉跟随当前激活空间。"""
        self._reference_db.load()
        combo = self.reference_space_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(self._reference_db.get_spaces())
        combo.setCurrentText(self._reference_db.get_active_space())
        combo.blockSignals(False)

    def _on_reference_space_changed(self, index: int):
        """主页面选取图库空间即激活；失败时恢复实际激活项。"""
        if index < 0:
            return
        name = self.reference_space_combo.itemText(index)
        if not name or name == self._reference_db.get_active_space():
            return
        if self._reference_db.set_active_space(name):
            logger.info(f"已切换到图库: {name}")
            return
        self.reference_space_combo.blockSignals(True)
        self.reference_space_combo.setCurrentText(
            self._reference_db.get_active_space())
        self.reference_space_combo.blockSignals(False)

    def _on_env_changed(self, index: int):
        """环境选择器切换：持久化 + 刷新工作流下拉框的"环境不支持"提示

        下拉框是应用内环境状态；切换后持久化到 session，并基于新的内存值
        刷新工作流下拉框提示。已启动的工作流持有自己的环境快照，不受影响。
        """
        if index < 0:
            return
        # 环境切换后参数定义可能改变；先提交旧面板里的可见字段，再按新环境
        # 重建。保存逻辑只更新找到的控件，不会覆盖另一环境的隐藏参数。
        if hasattr(self, "_save_displayed_params"):
            self._save_displayed_params()
        from ...core.config import save_env
        save_env(self._env_combo.itemData(index))
        self._load_workflow_configs()
        if hasattr(self, "_rebuild_param_panel"):
            self._rebuild_param_panel()

    def navigate_user(self, delta: int) -> None:
        """按 delta 偏移切换当前用户（-1 上一个 / +1 下一个）。

        边界夹止：到达列表首尾时不再移动。
        通过修改 user_combo.currentIndex 触发 _on_user_changed 完整链路。
        """
        count = self.user_combo.count()
        if count < 2:
            return
        new_idx = max(0, min(count - 1, self.user_combo.currentIndex() + delta))
        if new_idx != self.user_combo.currentIndex():
            self.user_combo.setCurrentIndex(new_idx)

    # ─── 布局选择器 ────────────────────────────────────────

    def _refresh_layout_combo(self, preferred_key: str = ""):
        """刷新布局列表，并优先保留调用方当前展示的选择。"""
        self.layout_combo.blockSignals(True)
        self.layout_combo.clear()
        selected = preferred_key or self._layout_manager.get_active_layout_key()
        for entry in self._layout_manager.list_layout_entries():
            self.layout_combo.addItem(entry.name, entry.key)
        idx = self.layout_combo.findData(selected)
        if idx < 0:
            idx = self.layout_combo.findData(
                self._layout_manager.get_active_layout_key())
        if idx >= 0:
            self.layout_combo.setCurrentIndex(idx)
        self.layout_combo.blockSignals(False)
        self._update_layout_desc_label()

    def _on_layout_changed(self, index: int):
        """布局选择器切换"""
        if index < 0:
            return
        key = self.layout_combo.currentData()
        if key and key != self._layout_manager.get_active_layout_key():
            self._layout_manager.set_active_layout(key)
            logger.info(f"已切换到布局: {self.layout_combo.currentText()} ({key})")
        self._update_layout_desc_label()

    def _update_layout_desc_label(self):
        """更新布局描述标签"""
        key = self.layout_combo.currentData()
        desc = ""
        if key:
            layout = self._layout_manager.load_layout(key)
            if layout:
                desc = layout.desc
        self.layout_desc_label.setText(desc)

    # ─── 方案选择器 ────────────────────────────────────────

    def _refresh_plan_combo(self):
        """刷新方案下拉，并按 actives.plan 恢复选中态。"""
        from ...core.config.plans import (
            get_active_plan_id,
            load_plans,
            set_active_plan_id,
        )
        stored = get_active_plan_id()
        self.plan_combo.blockSignals(True)
        self.plan_combo.clear()
        self.plan_combo.addItem(PLAN_CUSTOM_LABEL, "")
        for plan in load_plans():
            self.plan_combo.addItem(plan.name, plan.id)
        idx = self.plan_combo.findData(stored)
        self.plan_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.plan_combo.blockSignals(False)
        if stored and idx < 0:
            # 上次选中的方案已被删除：说清楚，并把失效引用清掉。
            logger.warning(f"上次选中的方案 {stored} 已不存在，回到自定义")
            self.log_text.append(tr("[提示] 上次选中的方案已不存在，已回到自定义"))
            set_active_plan_id("")
        self._apply_selected_plan(persist=False)

    def _selected_plan(self):
        """当前下拉选中的方案；「自定义」或引用已失效时返回 None。"""
        from ...core.config.plans import load_plans
        plan_id = self.plan_combo.currentData()
        if not plan_id:
            return None
        for plan in load_plans():
            if plan.id == plan_id:
                return plan
        return None

    def _ensure_target_plan(self) -> None:
        """为闲置目标选择兼容方案，不改全局默认或其他目标的草稿。"""
        target = self._current_execution_target()
        plan = self._selected_plan()
        if (target is None or self._running or plan is None
                or plan.allows(target.kind)):
            return
        from ...core.config.plans import load_plans

        for candidate in load_plans():
            if not candidate.allows(target.kind):
                continue
            if ((candidate.space and self.reference_space_combo.findText(candidate.space) < 0)
                    or (candidate.env and self._env_combo.findData(candidate.env) < 0)
                    or (candidate.layout and self.layout_combo.findData(candidate.layout) < 0)):
                continue
            index = self.plan_combo.findData(candidate.id)
            if index < 0:
                continue
            blocked = self.plan_combo.blockSignals(True)
            self.plan_combo.setCurrentIndex(index)
            self.plan_combo.blockSignals(blocked)
            self._apply_selected_plan(persist=False)
            self._capture_launch_draft(target.id)
            self.log_text.append(
                tr("[执行目标] 当前方案不适用，已自动切换到「{name}」").format(
                    name=candidate.name))
            return
        self.log_text.append(tr("[提示] 没有适用于当前执行目标的方案，请配置连接方案"))

    def _on_plan_changed(self, index: int):
        """方案下拉切换：选方案则填充并锁定三个选择器，选自定义则放开。"""
        if index < 0:
            return
        self._apply_selected_plan(persist=True)

    def _apply_selected_plan(self, *, persist: bool) -> None:
        plan_id = self.plan_combo.currentData() or ""
        plan = self._selected_plan()
        if plan_id and plan is None:
            # 方案被删除但 actives.plan 还指着它：降级回自定义，别静默。
            logger.warning(f"方案 {plan_id} 已不存在，回到自定义")
            self.log_text.append(tr("[提示] 选中的方案已不存在，已回到自定义"))
            self.plan_combo.blockSignals(True)
            self.plan_combo.setCurrentIndex(0)
            self.plan_combo.blockSignals(False)
            plan_id = ""
        if persist:
            from ...core.config.plans import set_active_plan_id
            set_active_plan_id(plan_id)
        if plan is None:
            self._release_plan_context()
            return
        self._stash_custom_context()
        missing = self._apply_plan_context(plan)
        if missing:
            logger.warning(f"方案「{plan.name}」引用了不存在的内容: {missing}")
            self.log_text.append(
                tr("[提示] 方案「{name}」的 {missing} 已不存在，未能全部套用").format(
                    name=plan.name, missing="、".join(missing)))
        self._set_context_controls_locked(LOCK_REASON_PLAN, True)
        self._refresh_run_button()

    def _apply_plan_context(self, plan) -> list[str]:
        """把方案的三项写进选择器，返回未能套用的项名。"""
        missing: list[str] = []
        if plan.space:
            idx = self.reference_space_combo.findText(plan.space)
            if idx >= 0:
                self.reference_space_combo.setCurrentIndex(idx)
            else:
                missing.append(tr("图库"))
        if plan.env:
            idx = self._env_combo.findData(plan.env)
            if idx >= 0:
                self._env_combo.setCurrentIndex(idx)
            else:
                missing.append(tr("环境"))
        if plan.layout:
            idx = self.layout_combo.findData(plan.layout)
            if idx >= 0:
                self.layout_combo.setCurrentIndex(idx)
            else:
                missing.append(tr("布局"))
        return missing

    def _stash_custom_context(self) -> None:
        """进入方案前记下手上的自定义组合，切回自定义时原样还原。"""
        if getattr(self, "_custom_context", None) is not None:
            return
        self._custom_context = (
            self.reference_space_combo.currentText(),
            self._env_combo.currentData(),
            self.layout_combo.currentData(),
        )

    def _release_plan_context(self) -> None:
        """回到自定义：解锁三个选择器并还原进入方案前的组合。"""
        self._set_context_controls_locked(LOCK_REASON_PLAN, False)
        stashed = getattr(self, "_custom_context", None)
        self._custom_context = None
        if stashed is not None:
            space, env, layout = stashed
            idx = self.reference_space_combo.findText(space)
            if idx >= 0:
                self.reference_space_combo.setCurrentIndex(idx)
            idx = self._env_combo.findData(env)
            if idx >= 0:
                self._env_combo.setCurrentIndex(idx)
            idx = self.layout_combo.findData(layout)
            if idx >= 0:
                self.layout_combo.setCurrentIndex(idx)
        self._refresh_run_button()

    # ─── 自动化状态管理 ────────────────────────────────────

    def _begin_automation(
        self, name: str, *, username: str | None = None,
        execution_scope: str = "daily",
    ) -> bool:
        """开始自动化，返回是否成功。若已有自动化在运行则拒绝。"""
        hk = self._user_config.hotkeys
        if self._running or (self._current_worker is not None and self._current_worker.isRunning()):
            self.log_text.append(self._hotkey_status(
                tr("[拒绝] 已有自动化在运行中，请等待结束"),
                (hk.stop, tr("停止"))))
            self.statusBar().showMessage(self._hotkey_status(
                tr("自动化运行中"), (hk.stop, tr("停止"))))
            logger.warning(f"拒绝启动 {name}：已有自动化在运行")
            return False
        target = self._current_execution_target()
        if target is not None and target.kind == "windows" and target.window:
            # hwnd 是窗口身份，坐标原点可能在连接后被用户拖动；冻结运行快照前
            # 必须刷新一次，不能把“已连接”误解成矩形永远不变。
            if not self._refresh_window_rect(target.window):
                target.status = "offline"
                self._red_box_flash_timer.stop()
                self._overlay.hide_border()
                from ...core.app_controller import remove_connected_target
                remove_connected_target(target.id)
                self._refresh_execution_targets_ui()
                self._sync_active_target_compat()
                self._refresh_run_button()
                message = tr("窗口已消失或句柄失效，请重新定位")
                self.statusBar().showMessage(message)
                self.log_text.append(
                    tr("[启动失败] {name}：{message}").format(
                        name=target.display_name, message=message))
                return False
            target.width = int(target.window.get("width") or 0)
            target.height = int(target.window.get("height") or 0)
            self._target_window = target.window
        admin_error = self._plan_admin_requirement_error()
        if admin_error:
            self.statusBar().showMessage(admin_error)
            self._show_workflow_start_error(admin_error)
            return False
        aspect_error = self._layout_aspect_error()
        if aspect_error:
            self.statusBar().showMessage(aspect_error)
            self._show_workflow_start_error(aspect_error)
            return False
        if target is None:
            self._show_workflow_start_error(tr("当前没有可执行目标"))
            return False
        target_snapshot = target.snapshot()
        self._capture_launch_draft(target.id)
        run_username = str(
            getattr(self, "_execution_username_snapshot", "") or ""
            if username is None else username)
        decision, run_context = self._run_manager.try_begin(
            target=target_snapshot, username=run_username, name=name)
        if run_context is None:
            self.log_text.append(f"[拒绝] {decision.reason}")
            self.statusBar().showMessage(decision.reason)
            return False
        self._current_run_context = run_context
        run_context.metadata["execution_scope"] = execution_scope
        run_context.metadata["launch_draft"] = copy.deepcopy(target.launch_draft)
        run_context.lease = getattr(self, "_execution_lease", None)
        # RapidOCR/ONNX 的同实例并发安全没有契约保证；每个运行实例持有
        # 独立的懒加载引擎，避免多设备推理互相污染。
        from ...core.ocr import OCREngine
        run_context.ocr = OCREngine()
        self._stop_requested = False
        self._run_state = "running"
        registry = getattr(self, "_execution_targets", None)
        self._running_target_id = (
            registry.active_target_id if registry is not None else None)
        self._running_target_snapshot = target_snapshot
        from .execution_runs import RunState
        self._run_manager.set_state(run_context.task_run_id, RunState.RUNNING)
        self._emit_run_instance_state(run_context, RunState.RUNNING.value)
        # 暂停事件：set=运行，clear=暂停阻塞
        signal = getattr(self, "_pause_acknowledged", None)
        notify = (lambda: signal.emit(run_context.task_run_id)) \
            if signal is not None \
            else (lambda: self._on_pause_acknowledged(run_context.task_run_id))
        self._pause_event = _AcknowledgedPauseEvent(notify)
        self._pause_event.set()  # 初始为运行状态
        run_context.pause_event = self._pause_event
        self._refresh_run_button()
        self._refresh_pause_button()
        self.statusBar().showMessage(self._hotkey_status(
            f"{name} {tr('运行中')}", (hk.pause, tr("暂停")),
            (hk.stop, tr("停止"))))
        logger.info(f"开始自动化: {name}")
        return True

    def _plan_admin_requirement_error(self) -> str:
        """返回当前方案未满足的 Windows 管理员权限要求；空串表示通过。"""
        from ...core import platforms

        # 管理员提升是 Windows 的进程令牌语义。macOS/Linux 的桌面授权需要
        # 各自的能力门禁，不能拿这个开关代替。
        if not platforms.IS_WINDOWS:
            return ""
        plan = self._selected_plan()
        if plan is None or not plan.requires_admin:
            return ""
        elevated = platforms.is_process_elevated()
        if elevated is True:
            return ""
        if elevated is None:
            return tr(
                "无法确认律匠当前是否具有管理员权限。连接方案「{name}」要求管理员权限，"
                "为避免点击或键盘输入失效，本次执行已拒绝。请关闭律匠，以管理员身份重新启动后再试。"
            ).format(name=plan.name)
        return tr(
            "连接方案「{name}」要求管理员权限，但律匠当前未以管理员身份运行。"
            "Windows 会阻止低权限进程向高权限游戏窗口发送点击和键盘输入。"
            "请关闭律匠，右键选择「以管理员身份运行」，然后重新执行。"
        ).format(name=plan.name)

    def _layout_aspect_error(self) -> str:
        """返回画布宽高比不符合布局声明的说明；空串表示通过。

        布局声明了画布尺寸（如 20:9）却对不上，说明目标窗口或设备的画面形态跟这个
        布局压根不是一路的——最典型的是端游窗口模式选了「桌面全屏」，或者设备不是
        20:9。这种错配不会报错，只会让所有坐标整体偏移，所以在启动前拦住。

        未声明尺寸、未定位窗口、拿不到截图尺寸时一律放行：门禁只拦已知的错配，
        不拦「还不知道」。
        """
        from ...core.layout_config import canvas_aspect_deviation

        key = self.layout_combo.currentData()
        if not key:
            return ""
        layout = self._layout_manager.load_layout(key)
        if layout is None or not layout.aspect:
            return ""
        from ...core.layout_config import parse_aspect_ratio
        try:
            expected = parse_aspect_ratio(layout.aspect)
        except ValueError:
            return ""
        if expected is None:
            return ""
        capture = getattr(self, "_capture", None)
        if capture is None:
            return ""
        try:
            width, height = capture.get_capture_size()
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"取截图尺寸失败，跳过画布尺寸校验: {exc}")
            return ""
        canvas = layout.canvas
        canvas_w = canvas.w_ratio * float(width)
        canvas_h = canvas.h_ratio * float(height)
        deviation = canvas_aspect_deviation(expected, canvas_w, canvas_h)
        if deviation is None or deviation <= layout.aspect_tolerance:
            return ""
        return tr(
            "当前布局画布区域尺寸不符合预定义要求，请修改布局后重试。\n\n"
            "布局「{layout}」要求画布宽高比 {expected}，"
            "当前画布为 {width}×{height}（比例 {actual}），偏差 {deviation}，"
            "超出允许的 {tolerance}。"
        ).format(
            layout=layout.name,
            expected=layout.aspect,
            width=round(canvas_w),
            height=round(canvas_h),
            actual=f"{canvas_w / canvas_h:.4f}",
            deviation=f"{deviation * 100:.2f}%",
            tolerance=f"{layout.aspect_tolerance * 100:.2f}%",
        )

    def _end_automation(self, name: str, *, run_context=None):
        """结束自动化，恢复 UI 状态。由工作流线程实际结束后调用。"""
        context = run_context or getattr(self, "_current_run_context", None)
        is_current = context is getattr(self, "_current_run_context", None)
        was_stopped = (
            context.stop_event.is_set() if context is not None
            else self._stop_requested
        )
        stop_dialog = getattr(self, "_stop_confirmation_dialog", None)
        if is_current and stop_dialog is not None:
            stop_dialog.reject()
        helper = (
            context.ui_helper if context is not None
            else getattr(self, "_ui_helper", None)
        )
        if helper is not None:
            helper.close_active_dialog()
            helper.deleteLater()
            if getattr(self, "_ui_helper", None) is helper:
                self._ui_helper = None
            if context is not None:
                context.ui_helper = None
        if context is not None:
            from .execution_runs import RunState
            terminal_state = str(context.metadata.get("terminal_state") or "")
            if terminal_state == "failed":
                final_state = RunState.FAILED
            elif terminal_state == "interrupted" or was_stopped:
                final_state = RunState.INTERRUPTED
            else:
                final_state = RunState.COMPLETED
            management = getattr(context.engine, "_tuning_management", None)
            if management is not None:
                management.mark_run_done(context.task_run_id, final_state.value)
            self._emit_run_instance_state(context, final_state.value)
            self._run_manager.finish(
                context.task_run_id, final_state,
            )
            self._emit_concurrency_changed()
        if not is_current:
            refresh_targets = getattr(self, "_refresh_execution_targets_ui", None)
            if callable(refresh_targets):
                refresh_targets()
            logger.info(f"后台运行实例结束: {name}")
            return
        self._stop_requested = False
        self._run_state = "idle"
        # 确保 pause_event 为 set 状态，避免下次启动阻塞
        pause_event = getattr(self, '_pause_event', None)
        if pause_event is not None:
            pause_event.set()
        self._current_worker = None
        if context is not None:
            self._current_run_context = None
        self._running_target_id = None
        self._running_target_snapshot = None
        self._set_context_controls_locked(LOCK_REASON_BATCH, False)
        self._refresh_run_button()
        self._refresh_pause_button()
        banner = getattr(self, '_adb_banner', None)
        if banner is not None:
            banner.setVisible(False)
        self.statusBar().showMessage(f"{name} 已结束")
        logger.info(f"自动化结束: {name}")

    def _set_context_controls_locked(self, reason: str, locked: bool) -> None:
        """按锁定原因禁用顶部上下文选择器。

        批量锁环境、布局，外加方案下拉——切方案会连带改掉环境和布局，等于
        绕过这把锁（图库在批量期间切换属于既有行为，不在本次改动范围内）。
        方案锁则锁图库、环境、布局三个，因为它们正是方案定义的内容；方案
        下拉自己不能锁，否则用户无法切回自定义。
        """
        names = ("reference_space_combo", "_env_combo", "layout_combo") \
            if reason == LOCK_REASON_PLAN \
            else ("plan_combo", "_env_combo", "layout_combo")
        for name in names:
            combo = getattr(self, name, None)
            setter = getattr(combo, "set_locked", None)
            if setter is not None:
                setter(reason, locked)

    def _is_stopped(self) -> bool:
        """工作流回调：检查是否请求了停止"""
        run_context = getattr(self, "_current_run_context", None)
        if run_context is not None:
            return run_context.stop_event.is_set()
        return self._stop_requested

    def _resolve_dsl_workflow_path(self, flow_cfg: dict) -> Path | None:
        """解析 DSL 文件；缓存路径失效时按脚本 ID 重新发现一次。"""
        from ...workflows.discovery import resolve_workflow_path

        wf_file = str(flow_cfg.get("wf_file") or "")
        path, resolved_file = resolve_workflow_path(
            wf_file, str(flow_cfg.get("id") or "")
        )
        if path is not None and resolved_file != wf_file:
            flow_cfg["wf_file"] = resolved_file
        return path

    def _show_workflow_start_error(self, message: str):
        """报告启动前错误，但不因提示窗口禁用主界面。"""
        from PyQt6.QtWidgets import QMessageBox, QWidget

        self.log_text.append(f"[错误] {message}")
        logger.error(message)
        box = QMessageBox(
            QMessageBox.Icon.Critical,
            tr("无法启动工作流"),
            message,
            QMessageBox.StandardButton.Ok,
            self if isinstance(self, QWidget) else None,  # type: ignore[arg-type]
        )
        _prepare_modeless_dialog(box)
        self._workflow_start_error_dialog = box
        box.finished.connect(
            lambda _code, item=box: setattr(
                self, "_workflow_start_error_dialog", None
            ) if getattr(self, "_workflow_start_error_dialog", None) is item
            else None
        )
        _show_modeless_dialog(box)

    def _show_user_execution_busy(self, message: str) -> None:
        """日常任务选择的用户正被其他进程执行时给出明确提示。"""
        from PyQt6.QtWidgets import QMessageBox, QWidget

        QMessageBox.warning(
            self if isinstance(self, QWidget) else None,  # type: ignore[arg-type]
            tr("用户正在执行任务"), message,
        )

    def _create_ui_callback(self, run_context=None):
        """创建线程安全的任务交互回调。

        _UIHelper 常驻主线程并只展示非模态窗口；工作流线程用
        threading.Event 等待业务结果。这样 confirm/pause/choose/input
        可以暂停自动化步骤，但不会禁用主界面。notify 同时走弹窗
        （native_notify）和告警面板（_ui_callback → alert_panel）双通道。
        """
        import threading

        context = run_context or getattr(self, "_current_run_context", None)
        stop_check = (
            context.stop_event.is_set if context is not None
            else self._is_stopped
        )
        helper = _UIHelper(self, stop_check=stop_check)
        if context is not None:
            context.ui_helper = helper
        # 迁移期兼容仍依赖当前运行实例的旧消费者；并发开放前会删除。
        self._ui_helper = helper

        def callback(action: str, **kwargs):
            # 工作流在 F10 后才走到交互语句时直接取消，不再投递弹窗。
            if stop_check():
                return None
            done_event = threading.Event()
            req = {"action": action, "kwargs": kwargs,
                   "result": None, "done": done_event}
            helper.request.emit(req)
            done_event.wait()
            return req["result"]

        return callback

    def _run_resume_event(self, run_context=None):
        """返回某个运行实例自己的 ADB 恢复事件。

        不能直接用主窗口的 `_adb_resume_event`：它只在被选中目标自带
        resume_event 时才被覆盖，而窗口目标没有这个字段，于是切到窗口目标后
        它仍然指向上一台设备。那时停止窗口任务或点「恢复」，会把那台设备上
        正在等待重连的任务一起唤醒，让它继续去打已经死掉的连接。
        """
        context = run_context or getattr(self, "_current_run_context", None)
        if context is not None:
            # 窗口目标没有 ADB 等待，它的运行实例就该解析出 None。退回兼容
            # 字段等于把「这个任务没有等待」错当成「用上一台设备的等待」。
            snapshot = getattr(context, "target_snapshot", None)
            return getattr(snapshot, "resume_event", None)
        return getattr(self, "_adb_resume_event", None)

    def _request_stop(self, *, stop_confirmed: bool = False):
        """统一停止入口：立即进入结束中，再等工作线程收尾。"""
        # 暂停中点结束先二次确认：暂停/结束热键位置接近，容易手误
        if self._run_state == 'paused' and not stop_confirmed:
            self._confirm_stop_while_paused()
            return
        if not self._running:
            self.log_text.append(tr("[提示] 当前没有正在运行的自动化"))
            return
        if self._run_state == STATE_STOPPING:
            return
        was_paused = self._run_state in ('paused', STATE_PAUSING)
        self._stop_requested = True
        run_context = getattr(self, "_current_run_context", None)
        if run_context is not None:
            run_context.stop_event.set()
            from .execution_runs import RunState
            self._run_manager.set_state(run_context.task_run_id, RunState.STOPPING)
            self._emit_run_instance_state(run_context, RunState.STOPPING.value)
        self._run_state = STATE_STOPPING
        # 先刷按钮再做日志、唤醒和对话框收尾，避免日志控件重排等
        # 工作让用户产生「没点到」的感觉。
        self._refresh_run_button()
        self._refresh_pause_button()
        # 若处于暂停状态，唤醒工作流线程以便响应停止
        if was_paused:
            pause_event = getattr(self, '_pause_event', None)
            if pause_event is not None:
                pause_event.set()
        self.log_text.append(tr("[操作] 收到停止请求"))
        logger.info("收到停止请求")
        # 若工作流正阻塞在交互对话框上，主动关闭以便停止生效
        helper = (
            run_context.ui_helper if run_context is not None
            else self._ui_helper
        )
        if helper is not None:
            helper.close_active_dialog()
        # 若工作流正阻塞在 ADB 断连等待上，唤醒以便响应停止
        resume_event = self._run_resume_event(run_context)
        if resume_event is not None:
            resume_event.set()
        banner = getattr(self, '_adb_banner', None)
        if banner is not None:
            banner.setVisible(False)
        self.statusBar().showMessage(tr("停止中... | 等待当前步骤结束"))
        # 占位主流程（_on_start）没有工作流线程，直接复位
        if self._current_worker is None:
            self._stop_requested = False
            self._run_state = 'idle'
            self._refresh_run_button()
            self._refresh_pause_button()
            self._overlay.set_color("red")
            self.log_text.append(tr("[操作] 已停止"))

    def _confirm_stop_while_paused(self) -> None:
        """异步确认停止：不启动嵌套事件循环，主界面始终可操作。"""
        if getattr(self, '_stop_confirm_pending', False):
            return
        from PyQt6.QtWidgets import QMessageBox, QWidget

        box = QMessageBox(
            QMessageBox.Icon.Question,
            tr("确认停止"), tr("任务暂停中，是否直接停止？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            self if isinstance(self, QWidget) else None,  # type: ignore[arg-type]
        )
        box.setDefaultButton(QMessageBox.StandardButton.No)
        _prepare_modeless_dialog(box)
        self._stop_confirm_pending = True
        self._stop_confirmation_dialog = box
        worker = self._current_worker

        def finished(_code: int) -> None:
            if getattr(self, '_stop_confirmation_dialog', None) is not box:
                return
            self._stop_confirmation_dialog = None
            self._stop_confirm_pending = False
            clicked = box.clickedButton()
            if (clicked is not None
                    and box.standardButton(clicked) == QMessageBox.StandardButton.Yes
                    and self._running and self._current_worker is worker):
                self._request_stop(stop_confirmed=True)

        box.finished.connect(finished)
        _show_modeless_dialog(box)

    # ─── 暂停/恢复 ────────────────────────────────────────

    def _on_pause_resume(self):
        """暂停/恢复按钮点击处理（暂停热键也转发到这里）"""
        # 停止确认弹窗打开期间忽略暂停热键，避免与二次确认竞态
        # （见 _confirm_stop_while_paused 的说明）
        if getattr(self, '_stop_confirm_pending', False):
            return
        run_state = getattr(self, '_run_state', 'idle')
        if run_state == 'running':
            self._request_pause()
        elif run_state == 'paused':
            self._resume_execution()

    def _request_pause(self):
        """暂停执行：阻塞工作流线程，保留调用栈"""
        if getattr(self, '_run_state', 'idle') != 'running':
            return
        self._run_state = STATE_PAUSING
        run_context = getattr(self, "_current_run_context", None)
        if run_context is not None:
            from .execution_runs import RunState
            self._run_manager.set_state(run_context.task_run_id, RunState.PAUSING)
            self._emit_run_instance_state(run_context, RunState.PAUSING.value)
        pause_event = getattr(self, '_pause_event', None)
        if pause_event is not None:
            pause_event.clear()  # 阻塞工作流线程
        self._refresh_pause_button()
        self._refresh_run_button()  # 广播 "pausing" 状态给其他入口
        # 请求已发出，但要等工作线程第一次观察到 clear，才进入 paused。
        hk = self._user_config.hotkeys
        paused_status = self._hotkey_status(
            tr("暂停中..."), (hk.pause, tr("恢复")), (hk.stop, tr("停止")))
        self.log_text.append(f"{tr('[操作] ')}{paused_status}")
        self.statusBar().showMessage(paused_status)
        logger.info("工作流暂停中")

    def _on_pause_acknowledged(self, task_run_id: str = "") -> None:
        """主线程槽：工作流已走到暂停检查点，正式进入 paused。"""
        run_context = (
            self._run_manager.run(task_run_id) if task_run_id
            else getattr(self, "_current_run_context", None)
        ) if hasattr(self, "_run_manager") else None
        if run_context is None:
            if task_run_id or getattr(self, '_run_state', 'idle') != STATE_PAUSING:
                return
            self._run_state = 'paused'
            self._refresh_pause_button()
            self._refresh_run_button()
            return
        if run_context.state.value != STATE_PAUSING:
            return
        from .execution_runs import RunState
        self._run_manager.set_state(run_context.task_run_id, RunState.PAUSED)
        self._emit_run_instance_state(run_context, RunState.PAUSED.value)
        if run_context is not getattr(self, "_current_run_context", None):
            self._refresh_execution_targets_ui()
            return
        self._run_state = 'paused'
        self._refresh_pause_button()
        self._refresh_run_button()
        hk = self._user_config.hotkeys
        paused_status = self._hotkey_status(
            tr("已暂停"), (hk.pause, tr("恢复")), (hk.stop, tr("停止")))
        self.log_text.append(f"{tr('[操作] ')}{paused_status}")
        self.statusBar().showMessage(paused_status)
        logger.info("工作流已暂停")

    def _resume_execution(self):
        """恢复执行：唤醒工作流线程，从暂停点继续"""
        if getattr(self, '_run_state', 'idle') != 'paused':
            return
        self._run_state = 'running'
        run_context = getattr(self, "_current_run_context", None)
        if run_context is not None:
            from .execution_runs import RunState
            self._run_manager.set_state(run_context.task_run_id, RunState.RUNNING)
            self._emit_run_instance_state(run_context, RunState.RUNNING.value)
        pause_event = getattr(self, '_pause_event', None)
        if pause_event is not None:
            pause_event.set()  # 唤醒工作流线程
        self._refresh_pause_button()
        self._refresh_run_button()  # 广播 "running" 状态给插件 Tab
        hk = self._user_config.hotkeys
        self.log_text.append(tr("[操作] 已恢复，继续执行..."))
        self.statusBar().showMessage(self._hotkey_status(
            tr("已恢复"), (hk.pause, tr("暂停")), (hk.stop, tr("停止"))))
        logger.info("工作流已恢复")

    def _refresh_pause_button(self):
        """刷新暂停/恢复按钮状态"""
        btn = getattr(self, 'btn_pause_resume', None)
        if btn is None:
            return
        run_state = getattr(self, '_run_state', 'idle')
        hk = self._user_config.hotkeys
        if other_task_running_label(self, "daily"):
            btn.setText(tr("暂停"))
            btn.setEnabled(False)
            apply_execution_button_style(btn, "disabled")
        elif run_state == 'running':
            btn.setText(self._hotkey_label(tr("暂停"), hk.pause))
            btn.setEnabled(True)
            apply_execution_button_style(btn, "pause")
        elif run_state == STATE_PAUSING:
            btn.setText(tr("暂停中"))
            btn.setEnabled(False)
            apply_execution_button_style(btn, "pausing")
        elif run_state == 'paused':
            btn.setText(self._hotkey_label(tr("恢复"), hk.pause))
            btn.setEnabled(True)
            apply_execution_button_style(btn, "run")
        else:  # idle
            btn.setText(tr("暂停"))
            btn.setEnabled(False)
            apply_execution_button_style(btn, "disabled")

    # ─── ADB 断连暂停恢复 ────────────────────────────────

    def _on_adb_connection_lost(self, target_id: str, error_msg: str):
        """某台 ADB 设备断连；仅运行目标可以打断当前任务。"""
        target = self._execution_targets.get(target_id)
        if target is None:
            return
        target.status = "offline"
        self._refresh_execution_targets_ui()
        run_context = self._run_manager.run_for_target(target_id)
        if run_context is None:
            if target_id == self._execution_targets.active_target_id:
                self._sync_active_target_compat()
                self._refresh_run_button()
            self.log_text.append(
                f"[警告] 非运行目标 {target.display_name} 已离线: {error_msg}")
            return
        from .execution_runs import RunState
        self._run_manager.set_state(
            run_context.task_run_id, RunState.WAITING_TARGET)
        self._emit_run_instance_state(
            run_context, RunState.WAITING_TARGET.value)
        message = tr(
            "[警告] ADB 连接异常，请恢复 {name} 的连接后点击恢复；"
            "若无法恢复，请按 F10 停止任务后重新连接: {error}").format(
                name=target.display_name, error=error_msg)
        self.log_text.append(message)
        if target_id != self._execution_targets.active_target_id:
            return
        self.statusBar().showMessage(tr(
            "ADB 异常，请恢复设备连接后点击恢复；无法恢复时请按 F10 停止"))
        banner = getattr(self, '_adb_banner', None)
        if banner is not None:
            label = getattr(self, '_adb_banner_label', None)
            if label is not None:
                label.setText(tr(
                    "⚠ ADB 连接异常：恢复设备连接后点击「恢复」；"
                    "无法恢复时请按 F10 停止任务"))
            btn = getattr(self, '_adb_banner_btn', None)
            if btn is not None:
                try:
                    btn.clicked.disconnect()
                except TypeError:
                    pass
                btn.clicked.connect(self._resume_adb)
            banner.setVisible(True)

    def _resume_adb(self):
        """用户点击「恢复」：唤醒工作流线程，重试失败的 ADB 命令"""
        # 横幅只为当前查看目标显示，因此唤醒的也必须是该目标的运行实例
        run_context = getattr(self, "_current_run_context", None)
        resume_event = self._run_resume_event(run_context)
        if resume_event is not None:
            resume_event.set()
            if run_context is not None:
                from .execution_runs import RunState
                next_state = (
                    RunState.PAUSED
                    if run_context.pause_event is not None
                    and not run_context.pause_event.is_set()
                    else RunState.RUNNING)
                self._run_manager.set_state(run_context.task_run_id, next_state)
                self._emit_run_instance_state(run_context, next_state.value)
            self.statusBar().showMessage(tr("已恢复，继续执行..."))
            self.log_text.append(tr("[操作] ADB 已恢复，工作流继续"))
        banner = getattr(self, '_adb_banner', None)
        if banner is not None:
            banner.setVisible(False)

    # ─── 后端就绪判定 ──────────────────────────────────

    def _current_execution_target(self):
        """读取显式执行目标；允许 RunControlMixin 独立测试宿主不提供注册表。"""
        registry = getattr(self, "_execution_targets", None)
        return registry.active() if registry is not None else None

    def _backend_ready(self) -> bool:
        """当前显式执行目标是否就绪。"""
        target = self._current_execution_target()
        if target is None and not hasattr(self, "_execution_targets"):
            if getattr(self, "_backend", None) == "adb":
                return bool(getattr(self, "_device_ready", False))
            return getattr(self, "_target_window", None) is not None
        return bool(target is not None and target.ready)

    def _plan_allows_backend(self) -> bool:
        """当前方案是否支持当前连接模式（自定义时永远放行）。"""
        plan = self._selected_plan()
        return plan is None or plan.allows(getattr(self, "_backend", None))

    def _backend_label(self) -> str:
        from ...core.config.plans import PLAN_MODE_ADB
        return (tr("ADB 模式")
                if getattr(self, "_backend", None) == PLAN_MODE_ADB
                else tr("窗口模式"))

    def _notify_plan_unsupported(self) -> None:
        """左下角状态栏 + 运行日志说明为什么开始执行是灰的。"""
        plan = self._selected_plan()
        message = tr("当前方案「{name}」不支持{mode}").format(
            name=plan.name if plan else "", mode=self._backend_label())
        self.log_text.append(f"[{tr('错误')}] {message}")
        self.statusBar().showMessage(message)

    # ─── 通用工作流执行 ────────────────────────────────────

    @guarded_launch(user_selector="_daily_execution_user_selector")
    def _on_run_workflow(self):
        """执行选中的工作流（异步）；运行中点击则作为停止按钮。"""
        # 运行中时该按钮文字为“停止 (F10)”，点击应触发停止而非重复启动
        if self._running:
            self._request_stop()
            return

        if not self._backend_ready():
            target = self._current_execution_target()
            if target is None:
                self.log_text.append(tr("[错误] 请先连接并选择执行目标"))
                self.statusBar().showMessage(tr("未连接 | 请先定位窗口或连接设备"))
            elif self._backend == "adb":
                self.log_text.append(tr("[错误] 请先连接设备"))
                self.statusBar().showMessage(tr("未连接设备 | 请先扫描并连接设备"))
            else:
                self.log_text.append(tr("[错误] 请先定位窗口"))
                self.statusBar().showMessage(tr("未定位窗口 | 请先扫描窗口并点击定位"))
            return

        # 连接方式确定之后才谈得上方案支不支持。
        if not self._plan_allows_backend():
            self._notify_plan_unsupported()
            return

        flow_cfg = self._get_selected_flow_config()
        if flow_cfg is None:
            self.log_text.append(tr("[错误] 请选择一个工作流"))
            return

        # env 限制检查：脚本声明了 env 且当前环境不在列表中，阻止执行
        current_env = self._selected_run_env()
        env_list = flow_cfg.get("env") or []
        if env_list:
            if current_env not in env_list:
                self.log_text.append(
                    tr("[错误] 当前工作环境 {env} 不在该脚本支持的环境 {envs} 中").format(
                        env=current_env, envs=", ".join(env_list)))
                return

        flow_name = flow_cfg["name"]
        flow_id = flow_cfg["id"]
        wf_class_name = flow_cfg.get("class", "")
        wf_path: Path | None = None
        if not wf_class_name:
            wf_path = self._resolve_dsl_workflow_path(flow_cfg)
            if wf_path is None:
                wf_file = flow_cfg.get("wf_file") or flow_id
                self._show_workflow_start_error(
                    tr("工作流文件不存在: {path}").format(path=wf_file))
                return

        username = self._execution_username_snapshot
        if not username:
            self.log_text.append(tr("[错误] 请选择有效的执行用户"))
            return

        if not self._begin_automation(flow_name, username=username):
            return

        run_context = self._current_run_context
        assert run_context is not None
        stop_check = run_context.stop_event.is_set

        layout_name = self.layout_combo.currentData()
        layout = self._layout_manager.load_layout(layout_name)

        if not layout:
            self.log_text.append(f"[错误] 无法加载布局: {layout_name}")
            self._end_automation(flow_name)
            return

        # 场景绑定校验：DSL 脚本由 engine 执行时按 AST 搜集场景自动校验；
        # 内置类脚本不做预校验（缺场景运行到该指令再报错）。

        # Windows 输入始终刷新目标窗口句柄：PostMessage 用它投递消息，
        # SendInput 用它在输入前激活正确窗口。
        # ADB 模式无窗口句柄，且坐标为设备物理像素（原点左上），window_left/top 恒为 0
        target_snapshot = self._running_target_snapshot
        assert target_snapshot is not None
        if target_snapshot.kind == "adb":
            window_left, window_top = 0, 0
        else:
            target_window = target_snapshot.window
            assert target_window is not None
            target_snapshot.input_ctrl.target_hwnd = target_window["hwnd"]
            window_left = target_window["left"]
            window_top = target_window["top"]

        engine = DeviceWorkflowEngineBuilder(
            capture=target_snapshot.capture,
            ocr=run_context.ocr,
            input_ctrl=target_snapshot.input_ctrl,
            layout=layout,
            input_sim=self._user_config.input_sim,
            delay_params=self._user_config.delay_params,
            android_apps=self._user_config.android_apps,
            android_device=target_snapshot.device,
            run_env=current_env,
            window_left=window_left,
            window_top=window_top,
            stop_check=stop_check,
            pause_event=self._pause_event,
        ).build()
        # session/context 初始化：启动时快照执行用户，全程只依赖此绑定值
        self._bind_engine_user(engine, username)
        from ...core.config.wf_configs import get_wf_config
        engine.workflow_config_snapshot = get_wf_config(flow_id)
        engine._ui_callback = self._create_ui_callback(run_context)
        engine.window_rebind_hook = (
            lambda window, target_id=run_context.target_id:
            self._on_target_window_rebound(target_id, window)
        )
        # 保存 engine 引用供完成回调使用
        self._current_engine = engine
        # 执行前先提交面板，再从统一解析器生成该用户的参数快照。
        if hasattr(self, '_save_displayed_params'):
            self._save_displayed_params()
        if flow_cfg.get("scope", "daily") == "daily":
            from ...core.task_params import resolve_task_params
            flow_params, _parameter_source = resolve_task_params(
                flow_id, username, flow_cfg.get("parameters", []),
                self._session_manager._users_dir,
            )
        else:
            # 专用脚本的参数面板由日常页隐藏，仍由专属配置页管理。
            from ...core.config.wf_configs import get_wf_config
            flow_params = get_wf_config(flow_cfg["id"]) or {}
        if hasattr(self, '_save_daily_config'):
            self._save_daily_config()

        self.log_text.append(f"[开始] {flow_name} 流程...")
        self.log_text.append(f"[执行用户] {username}")
        if flow_params:
            self.log_text.append(f"[参数] {flow_params}")

        # Python 代码工作流 vs DSL 工作流
        if wf_class_name:
            from ...workflows.implementations import get_workflow_class
            wf_class = get_workflow_class(wf_class_name)
            wf_instance = wf_class(
                capture=target_snapshot.capture,
                ocr=run_context.ocr,
                input_ctrl=target_snapshot.input_ctrl,
                layout=layout,
                input_sim=self._user_config.input_sim,
                delay_params=self._user_config.delay_params,
                window_left=window_left,
                window_top=window_top,
                stop_check=stop_check,
                pause_event=self._pause_event,
            )
            self._start_workflow(
                flow_id, flow_name,
                lambda: engine.execute(
                    wf_instance, initial_variables=flow_params),
                record_history=True, username=username, params=flow_params,
                task_scope=flow_cfg.get("scope", "daily"),
            )
        else:
            # DSL 路径已在进入运行态之前完成校验。
            assert wf_path is not None
            self._start_workflow(
                flow_id, flow_name,
                lambda: engine.execute(wf_path, initial_variables=flow_params),
                record_history=True, username=username, params=flow_params,
                task_scope=flow_cfg.get("scope", "daily"),
            )

    # ─── 异步工作流执行 ────────────────────────────────────

    def _start_workflow(
        self, flow_id: str, flow_name: str, workflow_fn, *,
        record_history: bool = False, username: str = "", params=None,
        task_scope: str = "daily",
    ):
        """启动工作流线程"""
        run_context = getattr(self, "_current_run_context", None)
        if run_context is None:
            raise RuntimeError("工作流启动时缺少运行上下文")
        run_context.metadata["output_started_at"] = datetime.now().astimezone()
        task_run = None
        if record_history:
            from ...core.daily_history import try_create_task_run
            target = run_context.target_snapshot
            target_kwargs = {
                "target_id": target.id,
                "target_kind": target.kind,
                "target_label": target.display_name,
                "environment": self._selected_run_env(),
                "layout": str(self.layout_combo.currentData() or ""),
                "input_kind": target.input_kind,
            }
            task_run = try_create_task_run(
                username=username or "default", task_id=flow_id,
                task_name=flow_name, task_scope=task_scope,
                params=params if params is not None else {}, source="single",
                task_run_id=run_context.task_run_id,
                started_at=run_context.metadata["output_started_at"],
                **target_kwargs)
        lease = run_context.lease
        def execute_authorized():
            if lease is None:
                return workflow_fn()
            with lease.authorized():
                return workflow_fn()
        worker = WorkflowWorker(flow_id, execute_authorized, task_run=task_run)
        run_context.worker = worker
        run_context.engine = getattr(self, "_current_engine", None)
        worker.finished.connect(
            lambda run_id=run_context.task_run_id:
            self._on_workflow_finished(run_id)
        )
        self._current_worker = worker  # type: ignore[assignment]  # 保持引用防止被垃圾回收
        # 在 worker 上附加 flow_name 以便日志显示
        worker._flow_name = flow_name
        try:
            worker.start()
        except Exception as exc:
            self._finish_task_run(worker, status="failed", error_message=str(exc))
            raise
        self._execution_started = True

    @guarded_finish
    def _on_workflow_finished(self, task_run_id: str):
        """线程退出后的工作流完成回调（在主线程执行）。"""
        run_context = self._run_manager.run(task_run_id)
        worker = run_context.worker if run_context is not None else None
        if not isinstance(worker, WorkflowWorker):
            logger.error(f"工作流完成时找不到运行实例: {task_run_id}")
            return
        flow_id = worker.flow_id
        result_or_exception = worker.result_or_exception
        flow_name = getattr(worker, '_flow_name', flow_id) if worker else flow_id

        if isinstance(result_or_exception, BaseException):
            run_context.metadata["terminal_state"] = "failed"
            self.log_text.append(f"[错误] {flow_name}流程异常退出: {result_or_exception}")
            logger.error(f"{flow_name}流程异常退出: {result_or_exception}")
            result_path = self._save_workflow_result(flow_id, {
                "error": str(result_or_exception),
                "exception_type": type(result_or_exception).__name__,
            }, task_run_id=task_run_id, run_context=run_context)
            self._finish_task_run(
                worker, status="failed", result_path=result_path,
                error_message=str(result_or_exception))
            # 异常不保存 session
        else:
            result = result_or_exception
            # 工作流预检失败返回 {"error": ...}：拒绝启动，不按正常完成处理
            if isinstance(result, dict) and result.get("error"):
                run_context.metadata["terminal_state"] = "failed"
                self.log_text.append(f"[错误] {flow_name}: {result['error']}")
                logger.error(f"工作流 {flow_id} 启动被拒绝: {result['error']}")
                result_path = self._save_workflow_result(
                    flow_id, result,
                    task_run_id=task_run_id, run_context=run_context)
                self._finish_task_run(
                    worker, status="failed", result_path=result_path,
                    error_message=str(result["error"]))
                return
            interrupted = run_context.stop_event.is_set()
            run_context.metadata["terminal_state"] = (
                "interrupted" if interrupted else "completed")
            if interrupted:
                # 中途停止（F10）是常态（如自动调律），已收集的结果
                # 照常落盘输出；仅不保存 session（中断点状态不完整）
                self.log_text.append(f"[已停止] {flow_name}流程被用户中断")
            else:
                # 正常结束 → 自动保存 session
                self._auto_save_session(run_context)
            result_path = self._save_workflow_result(
                flow_id, result, interrupted=interrupted,
                task_run_id=task_run_id, run_context=run_context)
            self._finish_task_run(
                worker,
                status="interrupted" if interrupted else "completed",
                result_path=result_path,
            )
            # 通用控制台输出；已有专用报告的工作流不再重复倾倒结果。
            _log_workflow_result(flow_id, result, interrupted=interrupted)
            if not interrupted:
                self.log_text.append(f"[完成] {flow_name} 结果已保存")

    @staticmethod
    def _finish_task_run(worker, **kwargs) -> None:
        task_run = getattr(worker, "task_run", None)
        if task_run is None:
            return
        try:
            task_run.finish(**kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"任务历史收尾失败，继续退出任务: {exc}")

    @staticmethod
    def _worker_task_run_id(worker) -> str:
        task_run = getattr(worker, "task_run", None)
        return str(getattr(task_run, "task_run_id", "") or "")

    def _auto_save_session(self, run_context=None):
        """正常结束时自动保存 session（存入启动时绑定的用户名）"""
        engine = (
            run_context.engine if run_context is not None
            else self._current_engine
        )
        if engine is not None and engine.run_username:
            self._session_manager.save(engine.run_username, engine.session)

    def _save_workflow_result(
        self, flow_id: str, result, interrupted: bool = False,
        task_run_id: str = "", run_context=None,
    ):
        """保存工作流结果到 session/output/{username}/YYYY-MM/DD/。

        中断（F10）的部分结果同样落盘，文件名带 _interrupted 后缀；
        即使结果为空也保留 JSON，确保历史记录始终能定位本次返回值。
        """
        if not isinstance(result, (dict, list)):
            return None
        try:
            serializable = _to_serializable(result)
            from ...constants import OUTPUT_DIR
            # 输出目录归属启动时绑定的用户名，不受运行期间 UI 切换影响
            engine = (
                run_context.engine if run_context is not None
                else self._current_engine
            )
            username = (engine.run_username if engine is not None else "") or "default"
            started_at = datetime.now()
            if run_context is not None:
                started_at = run_context.metadata["output_started_at"]
            user_output_dir = dated_output_dir(OUTPUT_DIR / username, started_at)
            user_output_dir.mkdir(parents=True, exist_ok=True)

            timestamp = started_at.strftime("%Y%m%d_%H%M%S_%f")
            suffix = "_interrupted" if interrupted else ""
            run_suffix = f"_{task_run_id}" if task_run_id else ""
            save_path = user_output_dir / (
                f"{flow_id}_{timestamp}{run_suffix}{suffix}.json")
            save_path.write_text(
                json.dumps(serializable, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"工作流结果保存失败，继续任务收尾: {exc}")
            self.log_text.append(f"[警告] {flow_id} 结果 JSON 保存失败: {exc}")
            return None
        logger.info(f"工作流结果已保存: {save_path}")
        self.log_text.append(f"[保存] {flow_id} → output/{save_path.relative_to(OUTPUT_DIR)}")
        return save_path

    # ─── 运行按钮 ──────────────────────────────────────────

    def _emit_run_instance_state(self, context, state: str) -> None:
        signal = getattr(self, "run_instance_state_changed", None)
        if signal is not None:
            signal.emit(context.task_run_id, context.target_id, state)
        self._emit_concurrency_changed()

    def _emit_concurrency_changed(self) -> None:
        signal = getattr(self, "concurrency_changed", None)
        manager = getattr(self, "_run_manager", None)
        if signal is not None and manager is not None:
            signal.emit(manager.active_count())

    def _sync_projected_context_locks(self) -> None:
        """按当前查看目标的运行实例刷新上下文专用锁。"""
        run_context = getattr(self, "_current_run_context", None)
        batch_locked = bool(
            self._running
            and run_context is not None
            and run_context.metadata.get("batch")
        )
        self._set_context_controls_locked(LOCK_REASON_BATCH, batch_locked)

    def _describe_running_user(self, username: str) -> str:
        """返回占用该用户的运行实例描述；没有占用返回空串。

        供批量等工作线程把「用户锁冲突」补成具体的目标和运行 ID。只读
        RunManager（内部自带锁），不碰任何控件。
        """
        manager = getattr(self, "_run_manager", None)
        if manager is None or not username:
            return ""
        for run in manager.all_runs():
            if run.username == username:
                return tr("占用目标 {target}，运行 {run_id}").format(
                    target=run.target_snapshot.display_name,
                    run_id=run.task_run_id[:8])
        return ""

    def _start_denial(self):
        """返回当前选中目标被并发门禁拒绝的理由；允许启动时返回 None。

        判定只能来自 RunManager（能力真源），UI 不另算一套布尔条件。这里传空
        用户名：用户执行锁属于「点下去才知道」的即时冲突，而目标占用和 Lv1
        是用户按下按钮前就该看到的状态。
        """
        manager = getattr(self, "_run_manager", None)
        registry = getattr(self, "_execution_targets", None)
        target_id = registry.active_target_id if registry is not None else None
        if manager is None or not target_id:
            return None
        decision = manager.can_start(target_id=target_id, username="")
        return None if decision.allowed else decision

    def start_denied_label(self) -> str:
        """并发门禁拒绝时运行按钮应显示的文字；允许启动时返回空串。

        插件页面的运行按钮也订阅同一个状态，文案只在这里定义一次。
        """
        denial = self._start_denial()
        if denial is None:
            return ""
        from .execution_runs import StartDenial
        return (tr("需激活 Lv1")
                if denial.denial_code == StartDenial.LV1_REQUIRED
                else tr("已有任务运行"))

    def _notify_start_denied(self) -> None:
        """左下角状态栏 + 运行日志说明为什么开始执行是灰的。"""
        decision = self._start_denial()
        if decision is None:
            return
        self.log_text.append(f"[{tr('拒绝')}] {decision.reason}")
        self.statusBar().showMessage(decision.reason)

    def _refresh_run_button(self):
        """根据运行状态和定位状态刷新运行按钮，并广播状态给插件页面。"""
        run_state = getattr(self, '_run_state', 'idle')
        hk = self._user_config.hotkeys
        if label := other_task_running_label(self, "daily"):
            state = run_state
            self.btn_run_workflow.setText(label)
            self.btn_run_workflow.setEnabled(False)
            apply_execution_button_style(self.btn_run_workflow, "disabled")
        elif run_state == STATE_STOPPING:
            state = STATE_STOPPING
            self.btn_run_workflow.setText(tr("停止中"))
            self.btn_run_workflow.setEnabled(False)
            apply_execution_button_style(self.btn_run_workflow, "stopping")
        elif self._running:
            state = run_state  # running 或 paused
            self.btn_run_workflow.setEnabled(True)
            self.btn_run_workflow.setText(self._hotkey_label(tr("停止"), hk.stop))
            apply_execution_button_style(self.btn_run_workflow, "stop")
        elif not self._backend_ready():
            state = "not_ready"
            self.btn_run_workflow.setEnabled(True)
            target = self._current_execution_target()
            label = tr("未连接") if target is None or self._backend == "adb" \
                else tr("未定位")
            self.btn_run_workflow.setText(label)
            apply_execution_button_style(self.btn_run_workflow, "not_ready")
        elif not self._plan_allows_backend():
            # 只置灰不 setEnabled(False)：禁用的控件收不到鼠标事件，点了就
            # 没有任何反馈，也就没法在左下角说明原因。
            state = STATE_PLAN_UNSUPPORTED
            self.btn_run_workflow.setEnabled(True)
            self.btn_run_workflow.setText(tr("方案不支持"))
            apply_execution_button_style(self.btn_run_workflow, "disabled")
        elif denied_label := self.start_denied_label():
            # 同「方案不支持」：只置灰不 setEnabled(False)，点击后在左下角
            # 说明原因，而不是让用户对着绿色按钮点完才知道要激活 Lv1。
            state = STATE_START_DENIED
            self.btn_run_workflow.setEnabled(True)
            self.btn_run_workflow.setText(denied_label)
            apply_execution_button_style(self.btn_run_workflow, "disabled")
        else:
            state = "idle"
            self.btn_run_workflow.setEnabled(True)
            self.btn_run_workflow.setText(self._hotkey_label(
                tr("开始执行"), hk.start))
            apply_execution_button_style(self.btn_run_workflow, "run")
        self._refresh_pause_button()
        self.automation_state_changed.emit(state)
        self._emit_concurrency_changed()
        # 批量锁原先只在批量启动/结束时直接改主窗口控件。多目标后这些
        # 控件是“当前查看目标”的投影：A 正在跑批量时切到空闲的 B，A 的
        # 锁不能继续残留在 B 上；切回 A 时又必须恢复。运行锁本来就是按
        # 当前目标计算的，批量锁也在同一个刷新点按当前 RunContext 重算。
        self._sync_projected_context_locks()
        for combo_name in ("plan_combo", "reference_space_combo", "_env_combo",
                           "layout_combo"):
            combo = getattr(self, combo_name, None)
            setter = getattr(combo, "set_locked", None)
            if setter is not None:
                setter(LOCK_REASON_RUNNING, self._running)
        workflow_combo = getattr(self, "workflow_combo", None)
        if workflow_combo is not None:
            workflow_combo.setEnabled(not self._running)
        param_panel = getattr(self, "_param_panel", None)
        if param_panel is not None:
            param_panel.setEnabled(not self._running)
        from ..execution_user_selector import ExecutionUserSelector
        for selector in self.findChildren(ExecutionUserSelector):
            selector.setEnabled(not self._running)
        target_list = getattr(self, "execution_target_list", None)
        if target_list is not None:
            # 目标列表始终可切换查看；运行实例持有自己的冻结快照，切换
            # 只改变主页面投影，不会让正在执行的工作流改道。
            target_list.setEnabled(True)
            target_list.setToolTip("")
            refresh_targets = getattr(self, "_refresh_execution_targets_ui", None)
            if callable(refresh_targets):
                refresh_targets()
        disconnect = getattr(self, "btn_disconnect_target", None)
        if disconnect is not None:
            disconnect.setEnabled(
                not self._running and self._current_execution_target() is not None)
        # 运行中只许扫描、不许定位/连接：定位会替换窗口目标并 stop 掉旧目标的
        # capture，而那正是运行中引擎持有的同一个对象
        if hasattr(self, "_apply_locate_lock"):
            self._apply_locate_lock()
        # 任务开始/结束/暂停恢复都会走到这里：顺带刷新"后台模式"开关的锁定态
        # （定位后可自由切换，仅任务运行期间锁定）
        if hasattr(self, "_refresh_bg_mode_lock"):
            self._refresh_bg_mode_lock()

    # ─── 启停控制 ──────────────────────────────────────────

    def _on_start(self):
        """开始执行（F9 快捷键转发）按当前左侧 Tab 分发：
        插件 Tab 实现 ``f9_run()`` 则交由其处理，否则走通用工作流。"""
        if self._running:
            return
        # F9 与托盘「开始」都走这里；不拦这一层，灰按钮就形同虚设。
        if not self._plan_allows_backend():
            self._notify_plan_unsupported()
            return
        if self._start_denial() is not None:
            self._notify_start_denied()
            return
        tabs = self._left_tabs
        widget = tabs.currentWidget() if tabs is not None else None
        runner = getattr(widget, 'f9_run', None)
        if callable(runner):
            runner()
        else:
            self._on_run_workflow()

    def _on_stop(self):
        """停止执行（转发到统一停止入口）"""
        self._request_stop()

    # ─── 插件工作流执行 ────────────────────────────────────

    @guarded_launch
    def run_workflow_implementation(
        self,
        impl_name: str,
        flow_name: str,
        configure,
        *,
        execution_username: str | None = None,
        history_params=None,
    ):
        """插件页面启动已注册工作流实现的通用入口（异步）。

        通用脚手架：backend/布局校验 → 创建引擎与工作流实例 →
        session 接线 → 回调 ``configure(wf_instance, engine)`` 由插件
        写入专属参数并输出开始日志 → 启动工作流线程。

        内置类工作流不做场景预校验（无 DSL AST 可静态搜集），
        缺场景时运行到对应指令再报错。
        """
        if self._running:
            self._request_stop()
            return

        username = (
            execution_username
            if execution_username is not None
            else self._user_manager.get_active_user_name()
        )
        if not username or username not in self._user_manager.list_users():
            self.log_text.append(tr("[错误] 请选择有效的执行用户"))
            return

        if not self._backend_ready():
            if self._current_execution_target() is None:
                self.log_text.append(tr("[错误] 请先连接并选择执行目标"))
            elif self._backend == "adb":
                self.log_text.append(tr("[错误] 请先连接设备"))
            else:
                self.log_text.append(tr("[错误] 请先定位窗口"))
            return

        if not self._begin_automation(
            flow_name, username=username, execution_scope=impl_name,
        ):
            return
        run_context = self._current_run_context
        assert run_context is not None
        stop_check = run_context.stop_event.is_set

        layout_name = self.layout_combo.currentData()
        layout = self._layout_manager.load_layout(layout_name)
        if not layout:
            self.log_text.append(f"[错误] 无法加载布局: {layout_name}")
            self._end_automation(flow_name)
            return

        # 窗口坐标
        target_snapshot = self._running_target_snapshot
        assert target_snapshot is not None
        if target_snapshot.kind == "adb":
            window_left, window_top = 0, 0
        else:
            target_window = target_snapshot.window
            assert target_window is not None
            target_snapshot.input_ctrl.target_hwnd = target_window["hwnd"]
            window_left = target_window["left"]
            window_top = target_window["top"]

        engine = DeviceWorkflowEngineBuilder(
            capture=target_snapshot.capture,
            ocr=run_context.ocr,
            input_ctrl=target_snapshot.input_ctrl,
            layout=layout,
            input_sim=self._user_config.input_sim,
            delay_params=self._user_config.delay_params,
            android_apps=self._user_config.android_apps,
            android_device=target_snapshot.device,
            run_env=self._selected_run_env(),
            window_left=window_left,
            window_top=window_top,
            stop_check=stop_check,
            pause_event=self._pause_event,
        ).build()
        engine.window_rebind_hook = (
            lambda window, target_id=run_context.target_id:
            self._on_target_window_rebound(target_id, window)
        )
        engine.task_run_id = run_context.task_run_id
        engine.execution_target_snapshot = target_snapshot
        self._bind_engine_user(engine, username)
        from ...core.config.wf_configs import get_wf_config
        engine.workflow_config_snapshot = get_wf_config(impl_name)
        engine._ui_callback = self._create_ui_callback(run_context)
        self._current_engine = engine  # type: ignore[assignment]
        from ...workflows.implementations import get_workflow_class
        wf_class = get_workflow_class(impl_name)
        wf_instance = wf_class(
            capture=target_snapshot.capture,
            ocr=run_context.ocr,
            input_ctrl=target_snapshot.input_ctrl,
            layout=layout,
            input_sim=self._user_config.input_sim,
            delay_params=self._user_config.delay_params,
            window_left=window_left,
            window_top=window_top,
            stop_check=stop_check,
            pause_event=self._pause_event,
        )

        # 插件写入专属参数（如判定器、部位选择等）并输出开始日志
        configure(wf_instance, engine)

        if history_params is None:
            run_context = getattr(wf_instance, "run_ctx", {})
            try:
                history_params = _to_history_snapshot(run_context)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"任务输入参数快照失败，使用文本快照: {exc}")
                history_params = {"run_context": str(run_context)}

        self._start_workflow(
            impl_name, flow_name, lambda: engine.execute(wf_instance),
            record_history=True, username=username,
            params=history_params if history_params is not None else {},
            task_scope="dedicated",
        )

    def _bind_engine_user(self, engine: WorkflowEngine, username: str) -> None:
        """Bind all user-scoped runtime data to the launch-time user snapshot."""
        engine.session = self._session_manager.load(username)
        engine.run_username = username
        from ...core.user_config import load_user_metadata
        user = load_user_metadata(username, self._session_manager._users_dir)
        engine.user_attributes_snapshot = {
            username: dict(user.attributes) if user is not None else {}
        }
        # context 由 execute() 自动初始化为空 dict。
        engine._save_callback = self._session_manager.save_fn(
            username, engine.session)
