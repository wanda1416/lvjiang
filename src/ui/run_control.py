"""运行控制混入类 - 用户/布局选择器、启停控制、工作流通用执行"""

import json
import traceback
from datetime import datetime
from pathlib import Path

from loguru import logger
from PyQt6.QtCore import QThread, pyqtSignal

import yaml

from ..constants import SYSTEM_WORKFLOWS_DIR, SYSTEM_CONFIG_DIR
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


class RunControlMixin:
    """运行控制混入类

    依赖主类提供:
        _user_manager, _session_manager, _layout_manager, _target_window,
        _running, _stop_requested, _capture, _ocr, _input, _overlay,
        user_combo, layout_combo, workflow_combo, btn_run_workflow,
        _param_panel, log_text, statusBar()
    """

    # ─── 工作流配置加载 ──────────────────────────────────

    def _load_workflow_configs(self):
        """读取 workflows.yaml，填充 workflow_combo"""
        self._workflow_configs: list[dict] = []
        self._loaded_flow_index: int | None = None   # 临时加载的外部工作流在列表中的位置
        yaml_path = SYSTEM_CONFIG_DIR / "workflows.yaml"

        if not yaml_path.exists():
            logger.warning(f"工作流配置文件不存在: {yaml_path}")
            return

        try:
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            flows = data.get("flows", [])
            for flow in flows:
                self._workflow_configs.append({
                    "id": flow["id"],
                    "name": flow["name"],
                    "wf_file": flow.get("wf_file", ""),
                    "class": flow.get("class", ""),
                    "required_scenes": flow.get("required_scenes", []),
                    "parameters": flow.get("parameters", []),
                })
        except Exception as e:
            logger.error(f"加载工作流配置失败: {e}")
            return

        # 填充下拉列表
        self.workflow_combo.clear()
        for cfg in self._workflow_configs:
            self.workflow_combo.addItem(cfg["name"], cfg["id"])

        logger.info(f"已加载 {len(self._workflow_configs)} 个工作流配置")

    def _on_load_workflow(self):
        """加载任意 .wf 文件为临时工作流项（非常驻，打开新文件会覆盖）

        名字/参数/可选项从 .wf 文件顶部的 `#%` front-matter 元数据提取。
        """
        from PyQt6.QtWidgets import QFileDialog
        from ..workflows.metadata import build_flow_config
        path, _ = QFileDialog.getOpenFileName(
            self, "加载工作流文件", str(SYSTEM_WORKFLOWS_DIR),
            "工作流文件 (*.wf);;所有文件 (*)",
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
        params = {}
        panel = getattr(self, '_param_panel', None)
        if panel is None:
            return params
        from PyQt6.QtWidgets import QComboBox, QSpinBox
        for param_def in flow_cfg.get("parameters", []):
            name = param_def["name"]
            # 先找 QSpinBox
            widget = panel.findChild(QSpinBox, name)
            if widget is not None:
                params[name] = str(widget.value())
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
            # 切换前：如果有正在运行的工作流，先保存旧用户的 session
            if getattr(self, '_current_engine', None) is not None and self._running:
                self._session_manager.save(old_name, self._current_engine.session)
                logger.info(f"用户切换前已保存 session: {old_name}")
            self._user_manager.set_active_user(name)
            logger.info(f"已切换到用户: {name}")
        # 刷新装备状态面板
        self._refresh_equip_status()

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
            self.log_text.append(f"[拒绝] 已有自动化在运行中，请等待结束或按 F10 停止")
            self.statusBar().showMessage("自动化运行中 | F10 停止")
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

    def _request_stop(self):
        """统一停止入口（F10 / 停止按钮）。只设标志，不立即改 running。"""
        self.log_text.append("[操作] 收到停止请求")
        logger.info("收到停止请求")
        if not self._running:
            self.log_text.append("[提示] 当前没有正在运行的自动化")
            return
        self._stop_requested = True
        self.statusBar().showMessage("停止中... | 等待当前步骤结束")
        # 占位主流程（_on_start）没有工作流线程，直接复位
        if self._current_worker is None:
            self._running = False
            self._stop_requested = False
            self._refresh_run_button()
            self._overlay.set_color("red")
            self.log_text.append("[操作] 已停止")

    # ─── 后端就绪判定 ──────────────────────────────────

    def _backend_ready(self) -> bool:
        """当前后端是否就绪（adb：设备已连接；windows：已定位窗口）"""
        if getattr(self, "_backend", "windows") == "adb":
            return bool(getattr(self, "_device_ready", False))
        return self._target_window is not None

    # ─── 通用工作流执行 ────────────────────────────────────

    def _on_run_workflow(self):
        """执行选中的工作流（异步）；运行中点击则作为停止按钮。"""
        # 运行中时该按钮文字为“停止 (F10)”，点击应触发停止而非重复启动
        if self._running:
            self._request_stop()
            return

        if not self._backend_ready():
            if getattr(self, "_backend", "windows") == "adb":
                self.log_text.append("[错误] 请先连接设备")
                self.statusBar().showMessage("未连接设备 | 请先扫描并连接设备")
            else:
                self.log_text.append("[错误] 请先定位窗口")
                self.statusBar().showMessage("未定位窗口 | 请先扫描窗口并点击定位")
            return

        flow_cfg = self._get_selected_flow_config()
        if flow_cfg is None:
            self.log_text.append("[错误] 请选择一个工作流")
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

        # 延迟校验：检查所需场景是否已绑定区域
        required_scenes = flow_cfg.get("required_scenes", [])
        if required_scenes:
            missing = self._layout_manager.check_scenes_valid(layout_name, required_scenes)
            if missing:
                names = "、".join(missing)
                self.log_text.append(f"[错误] 以下场景未绑定区域: {names}")
                self.statusBar().showMessage(f"场景缺失: {names}")
                self._end_automation(flow_name)
                return

        # 后台模式下（windows），刷新目标窗口句柄（窗口可能被重新打开导致 hwnd 变化）
        # ADB 模式无窗口句柄，且坐标为设备物理像素（原点左上），window_left/top 恒为 0
        if getattr(self, "_backend", "windows") == "adb":
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
            delay_config=self._user_config.input_delay,
            window_left=window_left,
            window_top=window_top,
            stop_check=self._is_stopped,
        )
        # session/context 初始化
        username = self._user_manager.get_active_user_name()
        engine.session = self._session_manager.load(username)
        # context 由 execute() 自动初始化为空 dict
        engine._save_callback = self._session_manager.save_fn(username, engine.session)
        # 保存 engine 引用供完成回调使用
        self._current_engine = engine
        flow_params = self._collect_flow_params()

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
                delay_config=self._user_config.input_delay,
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
                wf_path = SYSTEM_WORKFLOWS_DIR / wf_file
            self._start_workflow(flow_id, flow_name,
                                 lambda: engine.execute(wf_path, initial_variables=flow_params))

    # ─── 异步工作流执行 ────────────────────────────────────

    def _start_workflow(self, flow_id: str, flow_name: str, workflow_fn):
        """启动工作流线程"""
        worker = WorkflowWorker(flow_id, workflow_fn)
        worker.finished.connect(self._on_workflow_finished)
        self._current_worker = worker  # 保持引用防止被垃圾回收
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
            tag = "（用户中断，部分结果）" if interrupted else ""
            logger.info(f"工作流 {flow_id} 结果{tag}: {json.dumps(serializable, ensure_ascii=False, indent=2)}")
            if not interrupted:
                self.log_text.append(f"[完成] {flow_name} 结果已保存")

        self._end_automation(flow_name)

    def _auto_save_session(self):
        """正常结束时自动保存 session"""
        engine = getattr(self, '_current_engine', None)
        if engine is not None:
            username = self._user_manager.get_active_user_name()
            if username:
                self._session_manager.save(username, engine.session)

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
        username = self._user_manager.get_active_user_name() or "default"
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
        """根据运行状态和定位状态刷新运行按钮。"""
        if self._running:
            stop_text = "停止 (F10)"
            stop_style = "background-color: #f44336; color: white; font-weight: bold; padding: 8px;"
            self.btn_run_workflow.setText(stop_text)
            self.btn_run_workflow.setStyleSheet(stop_style)
            if hasattr(self, 'btn_run_tuning'):
                self.btn_run_tuning.setText("停止 (F10)")
                self.btn_run_tuning.setStyleSheet(
                    "background-color: #f44336; color: white; font-weight: bold; padding: 10px; font-size: 14px;"
                )
        elif not self._backend_ready():
            label = "未连接" if getattr(self, "_backend", "windows") == "adb" else "未定位"
            idle_style = "background-color: #FFC107; color: #333; font-weight: bold; padding: 8px;"
            self.btn_run_workflow.setText(label)
            self.btn_run_workflow.setStyleSheet(idle_style)
            if hasattr(self, 'btn_run_tuning'):
                self.btn_run_tuning.setText(label)
                self.btn_run_tuning.setStyleSheet(
                    "background-color: #FFC107; color: #333; font-weight: bold; padding: 10px; font-size: 14px;"
                )
        else:
            self.btn_run_workflow.setText("开始执行 (F9)")
            self.btn_run_workflow.setStyleSheet(
                "background-color: #4CAF50; color: white; font-weight: bold; padding: 8px;"
            )
            if hasattr(self, 'btn_run_tuning'):
                self.btn_run_tuning.setText("开始调律 (F9)")
                self.btn_run_tuning.setStyleSheet(
                    "background-color: #4CAF50; color: white; font-weight: bold; padding: 10px; font-size: 14px;"
                )

    # ─── 启停控制 ──────────────────────────────────────────

    def _on_start(self):
        """开始执行（F9 快捷键转发）根据当前左侧 Tab 分发"""
        if self._running:
            return
        # 检查当前 Tab：调律 Tab 索引为 1
        if hasattr(self, '_left_tabs') and self._left_tabs.currentIndex() == 1:
            self._on_run_tuning()
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

    # ─── 调律工作流执行 ────────────────────────────────────

    def _on_run_tuning(self):
        """执行自动调律工作流（异步）"""
        if self._running:
            self._request_stop()
            return

        if not self._backend_ready():
            if getattr(self, "_backend", "windows") == "adb":
                self.log_text.append("[错误] 请先连接设备")
            else:
                self.log_text.append("[错误] 请先定位窗口")
            return

        # 获取选中的部位
        selected_slots = self._get_tuning_selected_slots() if hasattr(self, '_get_tuning_selected_slots') else []
        if not selected_slots:
            self.log_text.append("[错误] 请至少选择一个调律部位")
            return

        # 获取流派配置（按流派分组的层级 dict）并创建判定器
        from src.apps.yysls.evaluator import (
            get_school_judge, get_school_rules, get_schools,
            is_school_implemented,
        )
        if hasattr(self, '_get_tuning_school_config'):
            schools_cfg = self._get_tuning_school_config()
        else:
            schools_cfg = {"huiyi_general": {"enabled": True}}
        enabled = {k: cfg for k, cfg in schools_cfg.items() if cfg.get("enabled")}
        if not enabled:
            self.log_text.append("[错误] 请至少选择一个调律流派")
            return
        school_judges = []
        school_rules = get_school_rules()
        keep_pvp = (self._get_tuning_keep_pvp()
                    if hasattr(self, '_get_tuning_keep_pvp') else False)
        skip_tuning = (self._get_tuning_skip_tuning()
                       if hasattr(self, '_get_tuning_skip_tuning') else False)
        for school, cfg in enabled.items():
            if not is_school_implemented(school):
                self.log_text.append(f"[警告] 流派「{get_schools().get(school, school)}」判定暂未实现，已跳过")
                continue
            rule = school_rules[school]
            wr_cfg = cfg.get("weapon_rules")
            if rule.weapon_rules and wr_cfg is not None and not wr_cfg:
                self.log_text.append(f"[错误] 流派「{rule.school_name}」需至少勾选一个武器规则")
                return
            school_judges.append(
                get_school_judge(school, {**cfg, "keep_pvp": keep_pvp}))
        if not school_judges:
            self.log_text.append("[错误] 选中的流派均未实现判定逻辑")
            return

        flow_name = "自动调律"
        if not self._begin_automation(flow_name):
            return

        layout_name = self._layout_manager.get_active_layout_name()
        layout = self._layout_manager.load_layout(layout_name)
        if not layout:
            self.log_text.append(f"[错误] 无法加载布局: {layout_name}")
            self._end_automation(flow_name)
            return

        # 检查所需场景
        required_scenes = [
            "game_main_page", "game_menu_page", "bag_equip_detail",
            "equip_weapon_detail", "equip_armor_detail",
        ]
        missing = self._layout_manager.check_scenes_valid(layout_name, required_scenes)
        if missing:
            names = "、".join(missing)
            self.log_text.append(f"[错误] 以下场景未绑定区域: {names}")
            self._end_automation(flow_name)
            return

        # 窗口坐标
        if getattr(self, "_backend", "windows") == "adb":
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
            delay_config=self._user_config.input_delay,
            window_left=window_left,
            window_top=window_top,
            stop_check=self._is_stopped,
        )
        username = self._user_manager.get_active_user_name()
        engine.session = self._session_manager.load(username)
        engine._save_callback = self._session_manager.save_fn(username, engine.session)
        self._current_engine = engine

        # 创建工作流实例
        from ..workflows.implementations import get_workflow_class
        wf_class = get_workflow_class("auto_tuning")
        wf_instance = wf_class(
            capture=self._capture,
            ocr=self._ocr,
            input_ctrl=self._input,
            layout=layout,
            delay_config=self._user_config.input_delay,
            window_left=window_left,
            window_top=window_top,
            stop_check=self._is_stopped,
        )
        wf_instance._selected_slots = selected_slots
        wf_instance._school_judges = school_judges
        # 潜力判定配置（形状与 single_tuning._load_school_config 一致，
        # 对齐 UI 实时勾选）：供 auto_tuning 的 judge_tuning_worthiness/
        # judge_equipment_potential 使用
        wf_instance._judge_configs = {
            k: {**cfg, "keep_pvp": keep_pvp} for k, cfg in enabled.items()}
        wf_instance._judge_schools = list(enabled)
        # 临时测试开关：跳过实际调律（仅模拟进出调律页，便于测试滚动）
        wf_instance._skip_tuning = skip_tuning
        # 调律说明文档（logs/tuning/）的操作用户名
        wf_instance._doc_username = username

        school_names = "、".join(j.school_name for j in school_judges)
        if keep_pvp:
            school_names += "（保留PVP）"
        if skip_tuning:
            self.log_text.append("[提示] 已开启「跳过实际调律」：仅模拟进出调律页，不执行调律")
        self.log_text.append(
            f"[开始] {flow_name} 流程，流派: {school_names}，部位: {selected_slots}")
        self._start_workflow("auto_tuning", flow_name,
                             lambda: engine.execute(wf_instance))
