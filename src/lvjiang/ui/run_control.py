"""运行控制混入类 - 用户/布局选择器、启停控制、工作流通用执行"""

import json
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger
from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ..core.config.resolver import get_resolver
from ..i18n import tr
from ..workflows.engine import WorkflowEngine


def _to_serializable(obj):
    """将包含 to_dict() 对象的列表/字典转为可 JSON 序列化的结构"""
    if isinstance(obj, list):
        return [_to_serializable(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _to_serializable(v) for k, v in obj.items()}
    if hasattr(obj, 'to_dict'):
        return obj.to_dict()
    return obj


class WorkflowWorker(QThread):
    """工作流异步执行线程"""
    finished = pyqtSignal(str, object)  # (flow_id, result_or_exception)

    def __init__(self, flow_id: str, fn, parent=None):
        super().__init__(parent)
        self.flow_id = flow_id
        self._fn = fn

    def run(self):
        try:
            result = self._fn()
            self.finished.emit(self.flow_id, result)
        except BaseException as e:
            tb = traceback.format_exc()
            logger.error(f"工作流 {self.flow_id} 异常退出:\n{tb}")
            self.finished.emit(self.flow_id, e)


class _UIHelper(QObject):
    """工作流线程 → 主线程的对话框桥（confirm/pause/input）

    请求以 dict 携带（信号用 object 签名，避免 QVariant 拷贝、保持引用）：
    主线程槽弹对话框 → 写 req["result"] → set req["done"]，
    工作流线程用 threading.Event 等待，无竞态、无需事件循环。
    槽是 QObject 方法，AutoConnection 跨线程投递行为确定为 Queued。
    """
    request = pyqtSignal(object)

    def __init__(self, window=None):
        super().__init__()
        self._window = window
        self._active_dialog = None
        self.request.connect(self._on_request)

    def _on_request(self, req: dict):
        """主线程：显示对话框并回填结果，无论成败都唤醒工作流线程"""
        try:
            req["result"] = self._show(req["action"], req["kwargs"])
        except Exception as e:
            logger.error(f"UI 交互对话框异常: {e}")
        finally:
            self._active_dialog = None
            req["done"].set()

    def _show(self, action: str, kwargs: dict):
        from PyQt6.QtWidgets import QInputDialog, QMessageBox
        if action == "confirm":
            box = QMessageBox(
                QMessageBox.Icon.Question, tr("工作流确认"),
                kwargs.get("message", ""),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                self._window,
            )
            self._active_dialog = box
            return box.exec() == QMessageBox.StandardButton.Yes
        if action == "confirm3":
            # 三选项确认对话框（用于材料不足等场景）
            box = QMessageBox(self._window)
            box.setIcon(QMessageBox.Icon.Question)
            box.setWindowTitle(tr("工作流确认"))
            box.setText(kwargs.get("message", ""))
            btn_continue = box.addButton(tr("继续调律"), QMessageBox.ButtonRole.AcceptRole)
            btn_skip = box.addButton(tr("跳过当前装备"), QMessageBox.ButtonRole.DestructiveRole)
            btn_end = box.addButton(tr("结束本次调律"), QMessageBox.ButtonRole.RejectRole)  # noqa: F841
            box.setDefaultButton(btn_continue)
            self._active_dialog = box
            box.exec()
            clicked = box.clickedButton()
            if clicked == btn_continue:
                return "continue"
            elif clicked == btn_skip:
                return "skip"
            else:  # btn_end or dialog closed
                return "end"
        if action == "pause":
            box = QMessageBox(
                QMessageBox.Icon.Information, tr("工作流暂停"),
                kwargs.get("message", ""),
                QMessageBox.StandardButton.Ok, self._window,
            )
            self._active_dialog = box
            box.exec()
            return None
        if action == "input":
            dlg = QInputDialog(self._window)
            dlg.setWindowTitle(tr("工作流输入"))
            dlg.setLabelText(kwargs.get("prompt", ""))
            self._active_dialog = dlg
            ok = dlg.exec()
            return dlg.textValue() if ok else None
        if action == "notify":
            # DSL notify: 写入告警面板（弹窗已在 builtin 层完成）
            message = kwargs.get("message", "")
            now = datetime.now()
            alert_id = f"dsl:notify:{now.strftime('%Y%m%d%H%M%S%f')}"
            # push_alert 内部调用 add_alert（含去重），同时更新 UI
            if self._window and getattr(self._window, 'alert_panel', None) is not None:
                self._window.alert_panel.push_alert(alert_id, message, now.isoformat())
            return None
        logger.warning(f"未知 UI 交互类型: {action}")
        return None

    def close_active_dialog(self):
        """主线程：关闭当前活动对话框（F10 停止时调用）

        confirm 返回 false、input 返回 null、pause 立即返回，
        使阻塞在对话框上的工作流能响应停止请求。
        """
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

    # ─── 工作流配置加载 ──────────────────────────────────

    def _load_workflow_configs(self):
        """发现全部脚本，按 workflows.yaml 的 exposed/overrides 过滤排序后填充下拉。

        脚本本体（.wf + 内置类）由发现层自动扫描；workflows.yaml 只决定日常页
        暴露哪些脚本、顺序、以及可选的显示名覆盖。暴露层逻辑与设备端
        悬浮面板共用 ``list_exposed_scripts()``。
        """
        from ..workflows.discovery import list_exposed_scripts

        self._workflow_configs: list[dict] = []
        self._loaded_flow_index: int | None = None   # 临时加载的外部工作流在列表中的位置

        try:
            self._workflow_configs = list_exposed_scripts()
        except Exception as e:
            logger.error(f"发现脚本失败: {e}")
            return

        # 填充下拉列表（block 信号，避免 addItem 逐条触发 _on_workflow_combo_changed）
        self.workflow_combo.blockSignals(True)
        self.workflow_combo.clear()
        for cfg in self._workflow_configs:
            self.workflow_combo.addItem(cfg["name"], cfg["id"])
        self.workflow_combo.blockSignals(False)

        # 初始化当前面板显示的脚本追踪（供日常配置持久化使用）
        first_cfg = self._workflow_configs[0] if self._workflow_configs else None
        self._displayed_script_id = first_cfg["id"] if first_cfg else None

        logger.info(f"已加载 {len(self._workflow_configs)} 个脚本配置")

    def _on_load_workflow(self):
        """加载任意 .wf 文件为临时工作流项（非常驻，打开新文件会覆盖）

        名字/参数/可选项从 .wf 文件顶部的 `#%` front-matter 元数据提取。
        """
        from PyQt6.QtWidgets import QFileDialog

        from ..workflows.metadata import build_flow_config
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
        else:
            self._workflow_configs.append(cfg)
            self.workflow_combo.addItem(cfg["name"], cfg["id"])
            self._loaded_flow_index = len(self._workflow_configs) - 1
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
        # 通知插件页面（如装备状态）刷新
        self.user_changed.emit(self._user_manager.get_active_user_name() or "")

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

    def _on_layout_changed(self, index: int):
        """布局选择器切换"""
        if index < 0:
            return
        name = self.layout_combo.currentText()
        if name and name != self._layout_manager.get_active_layout_name():
            self._layout_manager.set_active_layout(name)
            logger.info(f"已切换到布局: {name}")

    # ─── 自动化状态管理 ────────────────────────────────────

    def _begin_automation(self, name: str) -> bool:
        """开始自动化，返回是否成功。若已有自动化在运行则拒绝。"""
        if self._running or (self._current_worker is not None and self._current_worker.isRunning()):
            self.log_text.append(tr("[拒绝] 已有自动化在运行中，请等待结束或按 F10 停止"))
            self.statusBar().showMessage(tr("自动化运行中 | F10 停止"))
            logger.warning(f"拒绝启动 {name}：已有自动化在运行")
            return False
        self._running = True
        self._stop_requested = False
        self._refresh_run_button()
        # 运行期间保持按钮可点击，以便用户点击切换为停止（_on_run_workflow 内部判断 _running 后转发 _request_stop）
        self.statusBar().showMessage(f"{name} 运行中 | F10 停止")
        logger.info(f"开始自动化: {name}")
        return True

    def _end_automation(self, name: str):
        """结束自动化，恢复 UI 状态。由工作流线程实际结束后调用。"""
        self._running = False
        self._stop_requested = False
        self._current_worker = None
        self._refresh_run_button()
        self.statusBar().showMessage(f"{name} 已结束")
        logger.info(f"自动化结束: {name}")

    def _is_stopped(self) -> bool:
        """工作流回调：检查是否请求了停止"""
        return self._stop_requested

    def _create_ui_callback(self):
        """创建线程安全的 UI 交互回调（confirm/pause/input/notify）

        _UIHelper 常驻主线程，工作流线程发信号请求弹窗，
        用 threading.Event 等待结果，避免 QEventLoop 的
        "结果先于 exec() 到达"竞态。notify 同时走弹窗
        （native_notify）和告警面板（_ui_callback → alert_panel）双通道。
        """
        import threading

        helper = _UIHelper(self)
        self._ui_helper = helper

        def callback(action: str, **kwargs):
            done_event = threading.Event()
            req = {"action": action, "kwargs": kwargs,
                   "result": None, "done": done_event}
            helper.request.emit(req)
            done_event.wait()
            return req["result"]

        return callback

    def _request_stop(self):
        """统一停止入口（F10 / 停止按钮）。只设标志，不立即改 running。"""
        self.log_text.append(tr("[操作] 收到停止请求"))
        logger.info("收到停止请求")
        if not self._running:
            self.log_text.append(tr("[提示] 当前没有正在运行的自动化"))
            return
        self._stop_requested = True
        # 若工作流正阻塞在交互对话框上，主动关闭以便停止生效
        helper = self._ui_helper
        if helper is not None:
            helper.close_active_dialog()
        self.statusBar().showMessage(tr("停止中... | 等待当前步骤结束"))
        # 占位主流程（_on_start）没有工作流线程，直接复位
        if self._current_worker is None:
            self._running = False
            self._stop_requested = False
            self._refresh_run_button()
            self._overlay.set_color("red")
            self.log_text.append(tr("[操作] 已停止"))

    # ─── 后端就绪判定 ──────────────────────────────────

    def _backend_ready(self) -> bool:
        """当前后端是否就绪（adb：设备已连接；windows：已定位窗口）"""
        if self._backend == "adb":
            return bool(self._device_ready)
        return self._target_window is not None

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

        flow_cfg = self._get_selected_flow_config()
        if flow_cfg is None:
            self.log_text.append(tr("[错误] 请选择一个工作流"))
            return

        flow_name = flow_cfg["name"]
        flow_id = flow_cfg["id"]

        if not self._begin_automation(flow_name):
            return

        layout_name = self._layout_manager.get_active_layout_name()
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
            window_left=window_left,
            window_top=window_top,
            stop_check=self._is_stopped,
        )
        # session/context 初始化：启动时快照当前用户，全程只依赖此绑定值
        username = self._user_manager.get_active_user_name()
        engine.session = self._session_manager.load(username)
        engine.run_username = username
        # context 由 execute() 自动初始化为空 dict
        engine._save_callback = self._session_manager.save_fn(username, engine.session)
        engine._ui_callback = self._create_ui_callback()
        # 保存 engine 引用供完成回调使用
        self._current_engine = engine
        flow_params = self._collect_flow_params()
        # 专用脚本的参数面板由日常页隐藏，执行时从 wf_configs 加载
        if not flow_params and flow_cfg.get("scope", "daily") != "daily":
            from ..core.config.wf_configs import get_wf_config
            flow_params = get_wf_config(flow_cfg["id"]) or {}
        # 执行前持久化当前参数，确保下次启动恢复最新值
        if hasattr(self, '_save_displayed_params'):
            self._save_displayed_params()
        if hasattr(self, '_save_daily_config'):
            self._save_daily_config()

        self.log_text.append(f"[开始] {flow_name} 流程...")
        if flow_params:
            self.log_text.append(f"[参数] {flow_params}")

        # Python 代码工作流 vs DSL 工作流
        wf_class_name = flow_cfg.get("class", "")
        if wf_class_name:
            from ..workflows.implementations import get_workflow_class
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
            )
            self._start_workflow(flow_id, flow_name,
                                 lambda: engine.execute(wf_instance, initial_variables=flow_params))
        else:
            # 原有 DSL 路径
            wf_file = flow_cfg["wf_file"]
            wf_path = Path(wf_file)
            if not wf_path.is_absolute():
                resolved = get_resolver().resolve_read(f"workflows/{wf_file}")
                if resolved is None:
                    logger.error(f"工作流文件不存在: {wf_file}")
                    return
                wf_path = resolved
            self._start_workflow(flow_id, flow_name,
                                 lambda: engine.execute(wf_path, initial_variables=flow_params))

    # ─── 异步工作流执行 ────────────────────────────────────

    def _start_workflow(self, flow_id: str, flow_name: str, workflow_fn):
        """启动工作流线程"""
        worker = WorkflowWorker(flow_id, workflow_fn)
        worker.finished.connect(self._on_workflow_finished)
        self._current_worker = worker  # type: ignore[assignment]  # 保持引用防止被垃圾回收
        # 在 worker 上附加 flow_name 以便日志显示
        worker._flow_name = flow_name
        worker.start()

    def _on_workflow_finished(self, flow_id: str, result_or_exception):
        """工作流完成回调（在主线程执行）"""
        # 从 worker 获取 flow_name
        worker = self.sender()
        flow_name = getattr(worker, '_flow_name', flow_id) if worker else flow_id

        if isinstance(result_or_exception, BaseException):
            self.log_text.append(f"[错误] {flow_name}流程异常退出: {result_or_exception}")
            logger.error(f"{flow_name}流程异常退出: {result_or_exception}")
            # 异常不保存 session
        else:
            result = result_or_exception
            # 工作流预检失败返回 {"error": ...}：拒绝启动，不按正常完成处理
            if isinstance(result, dict) and result.get("error"):
                self.log_text.append(f"[错误] {flow_name}: {result['error']}")
                logger.error(f"工作流 {flow_id} 启动被拒绝: {result['error']}")
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
            self._save_workflow_result(flow_id, result, interrupted=interrupted)
            # 通用控制台输出
            serializable = _to_serializable(result)
            tag = tr("（用户中断，部分结果）") if interrupted else ""
            logger.info(f"工作流 {flow_id} 结果{tag}: {json.dumps(serializable, ensure_ascii=False, indent=2)}")
            if not interrupted:
                self.log_text.append(f"[完成] {flow_name} 结果已保存")

        self._end_automation(flow_name)

    def _auto_save_session(self):
        """正常结束时自动保存 session（存入启动时绑定的用户名）"""
        engine = self._current_engine
        if engine is not None and engine.run_username:
            self._session_manager.save(engine.run_username, engine.session)

    def _save_workflow_result(self, flow_id: str, result, interrupted: bool = False):
        """保存工作流结果到 local/output/{username}/{flow_id}_{timestamp}.json

        中断（F10）的部分结果同样落盘，文件名带 _interrupted 后缀；
        中断且尚无任何已收集数据时不产生空文件。
        """
        if not isinstance(result, (dict, list)):
            return
        if interrupted and not result:
            return

        serializable = _to_serializable(result)

        from ..constants import OUTPUT_DIR
        # 输出目录归属启动时绑定的用户名，不受运行期间 UI 切换影响
        engine = self._current_engine
        username = (engine.run_username if engine is not None else "") or "default"
        user_output_dir = OUTPUT_DIR / username
        user_output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = "_interrupted" if interrupted else ""
        save_path = user_output_dir / f"{flow_id}_{timestamp}{suffix}.json"
        save_path.write_text(
            json.dumps(serializable, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"工作流结果已保存: {save_path}")
        self.log_text.append(f"[保存] {flow_id} → output/{username}/{save_path.name}")

    # ─── 运行按钮 ──────────────────────────────────────────

    def _refresh_run_button(self):
        """根据运行状态和定位状态刷新运行按钮，并广播状态给插件页面。"""
        if self._running:
            state = "running"
            self.btn_run_workflow.setText(tr("停止 (F10)"))
            self.btn_run_workflow.setStyleSheet(
                "background-color: #f44336; color: white; font-weight: bold; padding: 8px; font-size: 13px; margin: 4px 0;"
            )
        elif not self._backend_ready():
            state = "not_ready"
            label = tr("未连接") if self._backend == "adb" else tr("未定位")
            self.btn_run_workflow.setText(label)
            self.btn_run_workflow.setStyleSheet(
                "background-color: #FFC107; color: #333; font-weight: bold; padding: 8px; font-size: 13px; margin: 4px 0;"
            )
        else:
            state = "ready"
            self.btn_run_workflow.setText(tr("开始执行 (F9)"))
            self.btn_run_workflow.setStyleSheet(
                "background-color: #4CAF50; color: white; font-weight: bold; padding: 8px; font-size: 13px; margin: 4px 0;"
            )
        self.automation_state_changed.emit(state)

    # ─── 启停控制 ──────────────────────────────────────────

    def _on_start(self):
        """开始执行（F9 快捷键转发）按当前左侧 Tab 分发：
        插件 Tab 实现 ``f9_run()`` 则交由其处理，否则走通用工作流。"""
        if self._running:
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

    def _on_toggle_running(self):
        """单按钮切换运行状态。"""
        if self._running:
            self._request_stop()
        else:
            self._on_start()

    # ─── 插件工作流执行 ────────────────────────────────────

    def run_workflow_implementation(self, impl_name: str, flow_name: str,
                                    configure):
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

        if not self._backend_ready():
            if self._backend == "adb":
                self.log_text.append(tr("[错误] 请先连接设备"))
            else:
                self.log_text.append(tr("[错误] 请先定位窗口"))
            return

        if not self._begin_automation(flow_name):
            return

        layout_name = self._layout_manager.get_active_layout_name()
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
            window_left=window_left,
            window_top=window_top,
            stop_check=self._is_stopped,
        )
        username = self._user_manager.get_active_user_name()
        engine.session = self._session_manager.load(username)
        engine.run_username = username
        engine._save_callback = self._session_manager.save_fn(username, engine.session)
        engine._ui_callback = self._create_ui_callback()
        self._current_engine = engine  # type: ignore[assignment]
        from ..workflows.implementations import get_workflow_class
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
        )

        # 插件写入专属参数（如判定器、部位选择等）并输出开始日志
        configure(wf_instance, engine)

        self._start_workflow(impl_name, flow_name,
                             lambda: engine.execute(wf_instance))
