"""运行控制混入类 - 用户/布局选择器、启停控制、工作流通用执行"""

import json
import threading
import traceback
from dataclasses import fields, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from loguru import logger
from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal

from ...core.config.resolver import get_resolver
from ...i18n import tr
from ...workflows.engine import WorkflowEngine

_RESULT_LOG_SUPPRESSED_FLOW_IDS = frozenset({"auto_tuning"})

# 顶部上下文选择器的锁定原因。定义在这里而不是 window.py：window 已经
# import 本模块，反向 import 会成环。
LOCK_REASON_BATCH = "batch"
LOCK_REASON_PLAN = "plan"

# 方案下拉的「不使用方案」项，userData 为空串。
PLAN_CUSTOM_LABEL = tr("- 自定义 -")

# automation_state_changed 的第四态：已连接，但当前方案不支持这种连接模式。
# 订阅方必须显式处理——它们的 else 分支都会把未知状态当成「就绪」。
STATE_PLAN_UNSUPPORTED = "plan_unsupported"

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

    Auto tuning already writes a Markdown tuning report.  Dumping the same
    ``tuning_reports`` payload again through loguru duplicates it in both the
    desktop console and the log file, often by thousands of lines.
    """
    if flow_id in _RESULT_LOG_SUPPRESSED_FLOW_IDS:
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
        dialog.setModal(False)
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
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
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

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
            box = QMessageBox(self._window)
            box.setIcon(QMessageBox.Icon.Information)
            box.setWindowTitle(tr("工作流暂停"))
            box.setText(kwargs.get("message", ""))
            continue_button = box.addButton(
                tr("继续"), QMessageBox.ButtonRole.AcceptRole
            )
            stop_button = box.addButton(
                tr("结束任务"), QMessageBox.ButtonRole.RejectRole
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
    _run_state = "idle"         # 运行状态：idle / running / paused
    _pause_event: threading.Event | None = None  # 暂停事件：set=运行，clear=暂停阻塞
    _stop_confirm_pending = False  # 暂停中点结束的二次确认弹窗是否正打开（挡暂停热键竞态）
    # 进入方案前暂存的自定义组合 (图库, 环境 key, 布局)；切回自定义时还原
    _custom_context: tuple[str, Any, str] | None = None

    @property
    def _running(self) -> bool:
        """运行状态派生自 _run_state（唯一事实来源）"""
        return getattr(self, '_run_state', 'idle') != 'idle'

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
        from ...workflows.discovery import list_exposed_scripts

        # 环境切换只会改变“不支持”提示，不应把用户选中的日常任务重置
        # 为第一项。清空 combo 前先按稳定 id 留住当前选择。
        selected_workflow_id = self.workflow_combo.currentData()
        if selected_workflow_id is None:
            selected_workflow_id = getattr(self, "_displayed_script_id", None)
        self._workflow_configs: list[dict] = []
        self._loaded_flow_index: int | None = None   # 临时加载的外部工作流在列表中的位置

        try:
            self._workflow_configs = list_exposed_scripts()
        except Exception as e:
            logger.error(f"发现脚本失败: {e}")
            return

        current_env = self._selected_run_env()

        # 填充下拉列表（block 信号，避免 addItem 逐条触发 _on_workflow_combo_changed）
        self.workflow_combo.blockSignals(True)
        self.workflow_combo.clear()
        for cfg in self._workflow_configs:
            full_display_name = cfg["name"]
            # env 限制检查：若脚本声明了 env 且当前环境不在列表中，追加提示
            env_list = cfg.get("env") or []
            if env_list and current_env not in env_list:
                full_display_name = (
                    f"{full_display_name} ({tr('环境不支持')})"
                )
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
        from PyQt6.QtWidgets import QCheckBox, QComboBox, QSpinBox, QWidget
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
        from ...core.config import save_env
        save_env(self._env_combo.itemData(index))
        self._load_workflow_configs()

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

    def _refresh_layout_combo(self):
        """刷新布局选择器下拉列表"""
        self.layout_combo.blockSignals(True)
        self.layout_combo.clear()
        layouts = self._layout_manager.list_layouts()
        active = self._layout_manager.get_active_layout_name()
        self.layout_combo.addItems(layouts)
        idx = self.layout_combo.findText(active)
        if idx >= 0:
            self.layout_combo.setCurrentIndex(idx)
        self.layout_combo.blockSignals(False)
        self._update_layout_desc_label()

    def _on_layout_changed(self, index: int):
        """布局选择器切换"""
        if index < 0:
            return
        name = self.layout_combo.currentText()
        if name and name != self._layout_manager.get_active_layout_name():
            self._layout_manager.set_active_layout(name)
            logger.info(f"已切换到布局: {name}")
        self._update_layout_desc_label()

    def _update_layout_desc_label(self):
        """更新布局描述标签"""
        name = self.layout_combo.currentText()
        desc = ""
        if name:
            layout = self._layout_manager.load_layout(name)
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
            idx = self.layout_combo.findText(plan.layout)
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
            self.layout_combo.currentText(),
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
            idx = self.layout_combo.findText(layout)
            if idx >= 0:
                self.layout_combo.setCurrentIndex(idx)
        self._refresh_run_button()

    # ─── 自动化状态管理 ────────────────────────────────────

    def _begin_automation(self, name: str) -> bool:
        """开始自动化，返回是否成功。若已有自动化在运行则拒绝。"""
        hk = self._user_config.hotkeys
        if self._running or (self._current_worker is not None and self._current_worker.isRunning()):
            self.log_text.append(
                f"{tr('[拒绝] 已有自动化在运行中，请等待结束或按')} {hk.stop} {tr('停止')}")
            self.statusBar().showMessage(f"{tr('自动化运行中')} | {hk.stop} {tr('结束')}")
            logger.warning(f"拒绝启动 {name}：已有自动化在运行")
            return False
        self._stop_requested = False
        self._run_state = "running"
        # 暂停事件：set=运行，clear=暂停阻塞
        self._pause_event = threading.Event()
        self._pause_event.set()  # 初始为运行状态
        self._refresh_run_button()
        self._refresh_pause_button()
        self.statusBar().showMessage(f"{name} {tr('运行中')} | {hk.pause} {tr('暂停')} | {hk.stop} {tr('结束')}")
        logger.info(f"开始自动化: {name}")
        return True

    def _end_automation(self, name: str):
        """结束自动化，恢复 UI 状态。由工作流线程实际结束后调用。"""
        helper = getattr(self, "_ui_helper", None)
        if helper is not None:
            helper.close_active_dialog()
            helper.deleteLater()
            self._ui_helper = None
        self._stop_requested = False
        self._run_state = "idle"
        # 确保 pause_event 为 set 状态，避免下次启动阻塞
        pause_event = getattr(self, '_pause_event', None)
        if pause_event is not None:
            pause_event.set()
        self._current_worker = None
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
        box.setModal(False)
        box.setWindowModality(Qt.WindowModality.NonModal)
        box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._workflow_start_error_dialog = box
        box.finished.connect(
            lambda _code, item=box: setattr(
                self, "_workflow_start_error_dialog", None
            ) if getattr(self, "_workflow_start_error_dialog", None) is item
            else None
        )
        box.show()
        box.raise_()
        box.activateWindow()

    def _create_ui_callback(self):
        """创建线程安全的任务交互回调。

        _UIHelper 常驻主线程并只展示非模态窗口；工作流线程用
        threading.Event 等待业务结果。这样 confirm/pause/choose/input
        可以暂停自动化步骤，但不会禁用主界面。notify 同时走弹窗
        （native_notify）和告警面板（_ui_callback → alert_panel）双通道。
        """
        import threading

        helper = _UIHelper(self, stop_check=self._is_stopped)
        self._ui_helper = helper

        def callback(action: str, **kwargs):
            # 工作流在 F10 后才走到交互语句时直接取消，不再投递弹窗。
            if self._is_stopped():
                return None
            done_event = threading.Event()
            req = {"action": action, "kwargs": kwargs,
                   "result": None, "done": done_event}
            helper.request.emit(req)
            done_event.wait()
            return req["result"]

        return callback

    def _request_stop(self):
        """统一停止入口（F10 / 结束按钮）。只设标志，不立即改 running。"""
        # 暂停中点结束先二次确认：暂停/结束热键位置接近，容易手误
        if self._run_state == 'paused' and not self._confirm_stop_while_paused():
            return
        self.log_text.append(tr("[操作] 收到停止请求"))
        logger.info("收到停止请求")
        if not self._running:
            self.log_text.append(tr("[提示] 当前没有正在运行的自动化"))
            return
        # 若处于暂停状态，唤醒工作流线程以便响应停止
        if self._run_state == 'paused':
            pause_event = getattr(self, '_pause_event', None)
            if pause_event is not None:
                pause_event.set()
            self._run_state = 'running'  # 避免停止窗口期内暂停热键误恢复
        self._stop_requested = True
        # 若工作流正阻塞在交互对话框上，主动关闭以便停止生效
        helper = self._ui_helper
        if helper is not None:
            helper.close_active_dialog()
        # 若工作流正阻塞在 ADB 断连等待上，唤醒以便响应停止
        resume_event = getattr(self, '_adb_resume_event', None)
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

    def _confirm_stop_while_paused(self) -> bool:
        """暂停中点击结束时弹二次确认，返回是否确认结束。

        弹窗期间用 _stop_confirm_pending 挡住暂停热键：QMessageBox.question 是
        Qt 主线程上的嵌套事件循环，但全局热键回调（pynput 监听线程）会
        直接同步调用 _on_pause_resume（不走 Qt 信号跨线程队列），不受
        这个嵌套事件循环阻塞，因此弹窗还开着时按暂停热键仍可能并发抢跑：
        此时 _run_state 尚未被状态借用改为 'running'，热键会被误判为
        "暂停中恢复" 而提前唤醒工作流线程——弹窗都没确认，线程却先动了。
        """
        self._stop_confirm_pending = True
        try:
            from PyQt6.QtWidgets import QMessageBox, QWidget
            reply = QMessageBox.question(
                self if isinstance(self, QWidget) else None,  # type: ignore[arg-type]
                tr("确认结束"), tr("任务暂停中，是否直接结束？"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            return reply == QMessageBox.StandardButton.Yes
        finally:
            self._stop_confirm_pending = False

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
        self._run_state = 'paused'
        pause_event = getattr(self, '_pause_event', None)
        if pause_event is not None:
            pause_event.clear()  # 阻塞工作流线程
        self._refresh_pause_button()
        self._refresh_run_button()  # 广播 "paused" 状态给插件 Tab
        # 请求已发出，但工作流线程可能仍在执行一个不可中断的原子操作
        # （如调律重置二次确认），未必已经真正阻塞，故用「暂停中」而非
        # 「已暂停」这种确定性措辞。
        hk = self._user_config.hotkeys
        self.log_text.append(f"{tr('[操作] 暂停中...')} | {hk.pause} {tr('恢复')} | {hk.stop} {tr('结束')}")
        self.statusBar().showMessage(f"{tr('暂停中...')} | {hk.pause} {tr('恢复')} | {hk.stop} {tr('结束')}")
        logger.info("工作流暂停中")

    def _resume_execution(self):
        """恢复执行：唤醒工作流线程，从暂停点继续"""
        if getattr(self, '_run_state', 'idle') != 'paused':
            return
        self._run_state = 'running'
        pause_event = getattr(self, '_pause_event', None)
        if pause_event is not None:
            pause_event.set()  # 唤醒工作流线程
        self._refresh_pause_button()
        self._refresh_run_button()  # 广播 "running" 状态给插件 Tab
        hk = self._user_config.hotkeys
        self.log_text.append(tr("[操作] 已恢复，继续执行..."))
        self.statusBar().showMessage(f"{tr('已恢复')} | {hk.pause} {tr('暂停')} | {hk.stop} {tr('结束')}")
        logger.info("工作流已恢复")

    def _refresh_pause_button(self):
        """刷新暂停/恢复按钮状态"""
        btn = getattr(self, 'btn_pause_resume', None)
        if btn is None:
            return
        run_state = getattr(self, '_run_state', 'idle')
        hk = self._user_config.hotkeys
        if run_state == 'running':
            btn.setText(f"{tr('暂停')} ({hk.pause})")
            btn.setEnabled(True)
            btn.setStyleSheet(
                "background-color: #FF9800; color: white; font-weight: bold; padding: 8px; font-size: 13px;"
            )
        elif run_state == 'paused':
            btn.setText(f"{tr('恢复')} ({hk.pause})")
            btn.setEnabled(True)
            btn.setStyleSheet(
                "background-color: #4CAF50; color: white; font-weight: bold; padding: 8px; font-size: 13px;"
            )
        else:  # idle
            btn.setText(tr("暂停"))
            btn.setEnabled(False)
            btn.setStyleSheet(
                "background-color: #9E9E9E; color: white; font-weight: bold; padding: 8px; font-size: 13px;"
            )

    # ─── ADB 断连暂停恢复 ────────────────────────────────

    def _refresh_running_engine_backends(self):
        """重连后把新的截图/输入后端同步给运行中的引擎

        工作流运行中断连重连时，引擎及其 BaseWorkflow 委托仍持有旧后端：
        scrcpy 截图后端的流已死，capture() 永远返回断连前的陈旧帧，OCR 全未命中。
        仅在引擎阻塞在 resume_event 等待时调用（此时替换引用无并发风险）。
        """
        engine = getattr(self, '_current_engine', None)
        if engine is None:
            return
        capture = getattr(self, '_capture', None)
        input_ctrl = getattr(self, '_input', None)
        if capture is not None:
            engine._capture = capture
        if input_ctrl is not None:
            engine._input = input_ctrl
        wf = getattr(engine, '_workflow', None)
        if wf is not None:
            if capture is not None:
                wf._capture = capture
            if input_ctrl is not None:
                wf._input = input_ctrl
        logger.info("[恢复] 已为运行中的引擎刷新截图/输入后端引用")

    def _on_adb_connection_lost(self, error_msg: str):
        """ADB 断连通知（主线程，由信号桥投递）"""
        self.log_text.append(f"[警告] ADB 连接异常，请重连设备后点击恢复: {error_msg}")
        self.statusBar().showMessage(tr("ADB 异常，请重连设备后点击恢复"))
        banner = getattr(self, '_adb_banner', None)
        if banner is not None:
            label = getattr(self, '_adb_banner_label', None)
            if label is not None:
                label.setText(tr("⚠ ADB 连接异常，请重连设备后点击右侧「恢复」"))
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
        # 直接用主窗口级别的 resume_event，不依赖 device 对象
        resume_event = getattr(self, '_adb_resume_event', None)
        if resume_event is not None:
            resume_event.set()
            self.statusBar().showMessage(tr("已恢复，继续执行..."))
            self.log_text.append(tr("[操作] ADB 已恢复，工作流继续"))
        banner = getattr(self, '_adb_banner', None)
        if banner is not None:
            banner.setVisible(False)

    # ─── 后端就绪判定 ──────────────────────────────────

    def _backend_ready(self) -> bool:
        """当前后端是否就绪（adb：设备已连接；windows：已定位窗口）"""
        if self._backend == "adb":
            return bool(self._device_ready)
        return self._target_window is not None

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

    def _on_run_workflow(self):
        """执行选中的工作流（异步）；运行中点击则作为停止按钮。"""
        # 运行中时该按钮文字为“停止 (F10)”，点击应触发停止而非重复启动
        if self._running:
            self._request_stop()
            return

        if not self._backend_ready():
            if self._backend == "adb":
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

        selector = getattr(self, "_daily_execution_user_selector", None)
        username = (
            selector.resolve_username()
            if selector is not None
            else self._user_manager.get_active_user_name()
        )
        if not username:
            self.log_text.append(tr("[错误] 请选择有效的执行用户"))
            return

        if not self._begin_automation(flow_name):
            return

        layout_name = self.layout_combo.currentText()
        layout = self._layout_manager.load_layout(layout_name)

        if not layout:
            self.log_text.append(f"[错误] 无法加载布局: {layout_name}")
            self._end_automation(flow_name)
            return

        # 场景绑定校验：DSL 脚本由 engine 执行时按 AST 搜集场景自动校验；
        # 内置类脚本不做预校验（缺场景运行到该指令再报错）。

        # 后台模式下（windows），刷新目标窗口句柄（窗口可能被重新打开导致 hwnd 变化）
        # ADB 模式无窗口句柄，且坐标为设备物理像素（原点左上），window_left/top 恒为 0
        if self._backend == "adb":
            window_left, window_top = 0, 0
        else:
            if self._input.background_mode and self._target_window:
                self._input.target_hwnd = self._target_window["hwnd"]
            window_left = self._target_window["left"]
            window_top = self._target_window["top"]

        engine = WorkflowEngine(
            capture=self._capture,
            ocr=self._ocr,
            input_ctrl=self._input,
            layout=layout,
            input_sim=self._user_config.input_sim,
            delay_params=self._user_config.delay_params,
            run_env=current_env,
            window_left=window_left,
            window_top=window_top,
            stop_check=self._is_stopped,
            pause_event=self._pause_event,
        )
        # session/context 初始化：启动时快照执行用户，全程只依赖此绑定值
        self._bind_engine_user(engine, username)
        engine._ui_callback = self._create_ui_callback()
        # 保存 engine 引用供完成回调使用
        self._current_engine = engine
        flow_params = self._collect_flow_params()
        # 专用脚本的参数面板由日常页隐藏，执行时从 wf_configs 加载
        if not flow_params and flow_cfg.get("scope", "daily") != "daily":
            from ...core.config.wf_configs import get_wf_config
            flow_params = get_wf_config(flow_cfg["id"]) or {}
        # 执行前持久化当前参数，确保下次启动恢复最新值
        if hasattr(self, '_save_displayed_params'):
            self._save_displayed_params()
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
                capture=self._capture,
                ocr=self._ocr,
                input_ctrl=self._input,
                layout=layout,
                input_sim=self._user_config.input_sim,
                delay_params=self._user_config.delay_params,
                window_left=window_left,
                window_top=window_top,
                stop_check=self._is_stopped,
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
        task_run = None
        if record_history:
            from ...core.daily_history import try_create_task_run
            task_run = try_create_task_run(
                username=username or "default", task_id=flow_id,
                task_name=flow_name, task_scope=task_scope,
                params=params if params is not None else {}, source="single")
        worker = WorkflowWorker(flow_id, workflow_fn, task_run=task_run)
        worker.finished.connect(self._on_workflow_finished)
        self._current_worker = worker  # type: ignore[assignment]  # 保持引用防止被垃圾回收
        # 在 worker 上附加 flow_name 以便日志显示
        worker._flow_name = flow_name
        worker.start()

    def _on_workflow_finished(self):
        """线程退出后的工作流完成回调（在主线程执行）。"""
        worker = self.sender()
        if not isinstance(worker, WorkflowWorker):
            logger.error("工作流完成信号来源不是 WorkflowWorker")
            return
        flow_id = worker.flow_id
        result_or_exception = worker.result_or_exception
        flow_name = getattr(worker, '_flow_name', flow_id) if worker else flow_id

        if isinstance(result_or_exception, BaseException):
            self.log_text.append(f"[错误] {flow_name}流程异常退出: {result_or_exception}")
            logger.error(f"{flow_name}流程异常退出: {result_or_exception}")
            result_path = self._save_workflow_result(flow_id, {
                "error": str(result_or_exception),
                "exception_type": type(result_or_exception).__name__,
            })
            self._finish_task_run(
                worker, status="failed", result_path=result_path,
                error_message=str(result_or_exception))
            # 异常不保存 session
        else:
            result = result_or_exception
            # 工作流预检失败返回 {"error": ...}：拒绝启动，不按正常完成处理
            if isinstance(result, dict) and result.get("error"):
                self.log_text.append(f"[错误] {flow_name}: {result['error']}")
                logger.error(f"工作流 {flow_id} 启动被拒绝: {result['error']}")
                result_path = self._save_workflow_result(flow_id, result)
                self._finish_task_run(
                    worker, status="failed", result_path=result_path,
                    error_message=str(result["error"]))
                self._end_automation(flow_name)
                return
            interrupted = self._stop_requested
            if interrupted:
                # 中途停止（F10）是常态（如自动调律），已收集的结果
                # 照常落盘输出；仅不保存 session（中断点状态不完整）
                self.log_text.append(f"[已停止] {flow_name}流程被用户中断")
            else:
                # 正常结束 → 自动保存 session
                self._auto_save_session()
            result_path = self._save_workflow_result(
                flow_id, result, interrupted=interrupted)
            self._finish_task_run(
                worker,
                status="interrupted" if interrupted else "completed",
                result_path=result_path,
            )
            # 通用控制台输出；已有专用报告的工作流不再重复倾倒结果。
            _log_workflow_result(flow_id, result, interrupted=interrupted)
            if not interrupted:
                self.log_text.append(f"[完成] {flow_name} 结果已保存")

        self._end_automation(flow_name)

    @staticmethod
    def _finish_task_run(worker, **kwargs) -> None:
        task_run = getattr(worker, "task_run", None)
        if task_run is None:
            return
        try:
            task_run.finish(**kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"任务历史收尾失败，继续退出任务: {exc}")

    def _auto_save_session(self):
        """正常结束时自动保存 session（存入启动时绑定的用户名）"""
        engine = self._current_engine
        if engine is not None and engine.run_username:
            self._session_manager.save(engine.run_username, engine.session)

    def _save_workflow_result(self, flow_id: str, result, interrupted: bool = False):
        """保存工作流结果到 local/output/{username}/{flow_id}_{timestamp}.json

        中断（F10）的部分结果同样落盘，文件名带 _interrupted 后缀；
        即使结果为空也保留 JSON，确保历史记录始终能定位本次返回值。
        """
        if not isinstance(result, (dict, list)):
            return None
        try:
            serializable = _to_serializable(result)
            from ...constants import OUTPUT_DIR
            # 输出目录归属启动时绑定的用户名，不受运行期间 UI 切换影响
            engine = self._current_engine
            username = (engine.run_username if engine is not None else "") or "default"
            user_output_dir = OUTPUT_DIR / username
            user_output_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            suffix = "_interrupted" if interrupted else ""
            save_path = user_output_dir / f"{flow_id}_{timestamp}{suffix}.json"
            save_path.write_text(
                json.dumps(serializable, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"工作流结果保存失败，继续任务收尾: {exc}")
            self.log_text.append(f"[警告] {flow_id} 结果 JSON 保存失败: {exc}")
            return None
        logger.info(f"工作流结果已保存: {save_path}")
        self.log_text.append(f"[保存] {flow_id} → output/{username}/{save_path.name}")
        return save_path

    # ─── 运行按钮 ──────────────────────────────────────────

    def _refresh_run_button(self):
        """根据运行状态和定位状态刷新运行按钮，并广播状态给插件页面。"""
        run_state = getattr(self, '_run_state', 'idle')
        hk = self._user_config.hotkeys
        if self._running:
            state = run_state  # running 或 paused
            self.btn_run_workflow.setText(f"{tr('结束')} ({hk.stop})")
            self.btn_run_workflow.setStyleSheet(
                "background-color: #f44336; color: white; font-weight: bold; padding: 8px; font-size: 13px;"
            )
        elif not self._backend_ready():
            state = "not_ready"
            label = tr("未连接") if self._backend == "adb" else tr("未定位")
            self.btn_run_workflow.setText(label)
            self.btn_run_workflow.setStyleSheet(
                "background-color: #FFC107; color: #333; font-weight: bold; padding: 8px; font-size: 13px;"
            )
        elif not self._plan_allows_backend():
            # 只置灰不 setEnabled(False)：禁用的控件收不到鼠标事件，点了就
            # 没有任何反馈，也就没法在左下角说明原因。
            state = STATE_PLAN_UNSUPPORTED
            self.btn_run_workflow.setText(tr("方案不支持"))
            self.btn_run_workflow.setStyleSheet(
                "background-color: #9E9E9E; color: white; font-weight: bold; padding: 8px; font-size: 13px;"
            )
        else:
            state = "idle"
            self.btn_run_workflow.setText(f"{tr('开始执行')} ({hk.start})")
            self.btn_run_workflow.setStyleSheet(
                "background-color: #4CAF50; color: white; font-weight: bold; padding: 8px; font-size: 13px;"
            )
        self.automation_state_changed.emit(state)
        from ..execution_user_selector import ExecutionUserSelector
        for selector in self.findChildren(ExecutionUserSelector):
            selector.setEnabled(not self._running)
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
            if self._backend == "adb":
                self.log_text.append(tr("[错误] 请先连接设备"))
            else:
                self.log_text.append(tr("[错误] 请先定位窗口"))
            return

        if not self._begin_automation(flow_name):
            return

        layout_name = self.layout_combo.currentText()
        layout = self._layout_manager.load_layout(layout_name)
        if not layout:
            self.log_text.append(f"[错误] 无法加载布局: {layout_name}")
            self._end_automation(flow_name)
            return

        # 窗口坐标
        if self._backend == "adb":
            window_left, window_top = 0, 0
        else:
            if self._input.background_mode and self._target_window:
                self._input.target_hwnd = self._target_window["hwnd"]
            window_left = self._target_window["left"]
            window_top = self._target_window["top"]

        engine = WorkflowEngine(
            capture=self._capture,
            ocr=self._ocr,
            input_ctrl=self._input,
            layout=layout,
            input_sim=self._user_config.input_sim,
            delay_params=self._user_config.delay_params,
            run_env=self._selected_run_env(),
            window_left=window_left,
            window_top=window_top,
            stop_check=self._is_stopped,
            pause_event=self._pause_event,
        )
        self._bind_engine_user(engine, username)
        engine._ui_callback = self._create_ui_callback()
        self._current_engine = engine  # type: ignore[assignment]
        from ...workflows.implementations import get_workflow_class
        wf_class = get_workflow_class(impl_name)
        wf_instance = wf_class(
            capture=self._capture,
            ocr=self._ocr,
            input_ctrl=self._input,
            layout=layout,
            input_sim=self._user_config.input_sim,
            delay_params=self._user_config.delay_params,
            window_left=window_left,
            window_top=window_top,
            stop_check=self._is_stopped,
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
        # context 由 execute() 自动初始化为空 dict。
        engine._save_callback = self._session_manager.save_fn(
            username, engine.session)
