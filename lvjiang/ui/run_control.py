"""运行控制混入类 - 用户/布局选择器、启停控制、毕业率执行"""

import traceback

from loguru import logger
from PyQt6.QtCore import QThread, pyqtSignal

from ..constants import SYSTEM_WORKFLOWS_DIR
from ..workflows.engine import WorkflowEngine


# 各工作流所需场景（用于启动前校验）
_GRADUATION_REQUIRED_SCENES = ["equip_bag_detail", "equip_weapon_detail", "equip_armor_detail"]
_TUNE_TEST_REQUIRED_SCENES = [
    "game_main_page", "game_menu_page", "equip_bag_detail",
    "equip_weapon_detail", "equip_tune_detail", "equip_tune_result",
]


class WorkflowWorker(QThread):
    """工作流异步执行线程"""
    finished = pyqtSignal(str, object)  # (name, result_or_exception)

    def __init__(self, name: str, fn, parent=None):
        super().__init__(parent)
        self.name = name
        self._fn = fn

    def run(self):
        try:
            result = self._fn()
            self.finished.emit(self.name, result)
        except BaseException as e:
            tb = traceback.format_exc()
            logger.error(f"工作流 {self.name} 异常退出:\n{tb}")
            self.finished.emit(self.name, e)


class RunControlMixin:
    """运行控制混入类

    依赖主类提供:
        _user_manager, _layout_manager, _target_window, _running, _stop_requested,
        _capture, _ocr, _input, _overlay,
        user_combo, layout_combo, btn_graduation, btn_tune_test, btn_run_toggle,
        flow_selector, mode_selector, log_text, statusBar()
    """

    # 工作流完成信号（由 MainWindow 定义）
    # workflow_finished = pyqtSignal(str, object)  # (name, result_or_error)

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
        if name and name != self._user_manager.get_active_user_name():
            self._user_manager.set_active_user(name)
            logger.info(f"已切换到用户: {name}")

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
        # 以 _current_worker 作为唯一真相：只要线程存在且在运行就拒绝
        if self._running or (self._current_worker is not None and self._current_worker.isRunning()):
            self.log_text.append(f"[拒绝] 已有自动化在运行中，请等待结束或按 F10 停止")
            self.statusBar().showMessage("自动化运行中 | F10 停止")
            logger.warning(f"拒绝启动 {name}：已有自动化在运行")
            return False
        self._running = True
        self._stop_requested = False
        self._refresh_run_button()
        self.btn_graduation.setEnabled(False)
        self.btn_tune_test.setEnabled(False)
        self.statusBar().showMessage(f"{name} 运行中 | F10 停止")
        logger.info(f"开始自动化: {name}")
        return True

    def _end_automation(self, name: str):
        """结束自动化，恢复 UI 状态。由工作流线程实际结束后调用。"""
        self._running = False
        self._stop_requested = False
        self._current_worker = None
        self._refresh_run_button()
        self.btn_graduation.setEnabled(True)
        self.btn_tune_test.setEnabled(True)
        self.statusBar().showMessage(f"{name} 已结束")
        logger.info(f"自动化结束: {name}")

    def _is_stopped(self) -> bool:
        """工作流回调：检查是否请求了停止"""
        return self._stop_requested

    def _request_stop(self):
        """统一停止入口（F10 / 停止按钮）。只设标志，不立即改 running。"""
        self.log_text.append("[操作] 收到 F10 停止请求")
        logger.info("收到 F10 停止请求")
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

    # ─── 毕业率按钮 ────────────────────────────────────────

    def _on_graduation(self):
        """执行装备分析流程（异步）"""
        if not self._target_window:
            self.log_text.append("[错误] 请先定位窗口")
            self.statusBar().showMessage("未定位窗口 | 请先扫描窗口并点击定位")
            return

        if not self._begin_automation("毕业率计算"):
            return

        layout_name = self._layout_manager.get_active_layout_name()
        layout = self._layout_manager.load_layout(layout_name)

        if not layout:
            self.log_text.append(f"[错误] 无法加载布局: {layout_name}")
            self._end_automation("毕业率计算")
            return

        # 延迟校验：检查所需场景是否已绑定区域
        missing = self._layout_manager.check_scenes_valid(layout_name, _GRADUATION_REQUIRED_SCENES)
        if missing:
            names = "、".join(missing)
            self.log_text.append(f"[错误] 以下场景未绑定区域: {names}")
            self.statusBar().showMessage(f"场景缺失: {names}")
            self._end_automation("毕业率计算")
            return

        engine = WorkflowEngine(
            capture=self._capture,
            ocr=self._ocr,
            input_ctrl=self._input,
            layout=layout,
            delay_config=self._user_config.delay,
            window_left=self._target_window["left"],
            window_top=self._target_window["top"],
            stop_check=self._is_stopped,
        )
        wf_path = SYSTEM_WORKFLOWS_DIR / "equip_analysis.wf"

        self.log_text.append("[开始] 装备分析流程...")
        self._start_workflow("毕业率计算", lambda: engine.run(wf_path))

    # ─── 单次调律测试按钮 ────────────────────────────────

    def _on_tune_test(self):
        """执行单次调律测试流程（异步）"""
        if not self._target_window:
            self.log_text.append("[错误] 请先定位窗口")
            self.statusBar().showMessage("未定位窗口 | 请先扫描窗口并点击定位")
            return

        if not self._begin_automation("单次调律测试"):
            return

        layout_name = self._layout_manager.get_active_layout_name()
        layout = self._layout_manager.load_layout(layout_name)

        if not layout:
            self.log_text.append(f"[错误] 无法加载布局: {layout_name}")
            self._end_automation("单次调律测试")
            return

        # 延迟校验：检查所需场景是否已绑定区域
        missing = self._layout_manager.check_scenes_valid(layout_name, _TUNE_TEST_REQUIRED_SCENES)
        if missing:
            names = "、".join(missing)
            self.log_text.append(f"[错误] 以下场景未绑定区域: {names}")
            self.statusBar().showMessage(f"场景缺失: {names}")
            self._end_automation("单次调律测试")
            return

        engine = WorkflowEngine(
            capture=self._capture,
            ocr=self._ocr,
            input_ctrl=self._input,
            layout=layout,
            delay_config=self._user_config.delay,
            window_left=self._target_window["left"],
            window_top=self._target_window["top"],
            stop_check=self._is_stopped,
        )
        wf_path = SYSTEM_WORKFLOWS_DIR / "tune_test.wf"

        self.log_text.append("[开始] 单次调律测试流程...")
        self._start_workflow("单次调律测试", lambda: engine.run(wf_path))

    # ─── 异步工作流执行 ────────────────────────────────

    def _start_workflow(self, name: str, workflow):
        """启动工作流线程"""
        worker = WorkflowWorker(name, workflow)
        worker.finished.connect(self._on_workflow_finished)
        self._current_worker = worker  # 保持引用防止被垃圾回收
        worker.start()

    def _on_workflow_finished(self, name: str, result_or_exception):
        """工作流完成回调（在主线程执行）"""
        if isinstance(result_or_exception, BaseException):
            self.log_text.append(f"[错误] {name}流程异常退出: {result_or_exception}")
            logger.error(f"{name}流程异常退出: {result_or_exception}")
        elif self._stop_requested:
            self.log_text.append(f"[已停止] {name}流程被用户中断")
        else:
            result = result_or_exception
            if name == "毕业率计算":
                self.log_text.append(f"[完成] 识别到 {len(result)} 件装备")
            elif name == "单次调律测试":
                self.log_text.append(f"[调律词条] {result}")
        self._end_automation(name)

    # ─── 运行按钮 ──────────────────────────────────────────

    def _refresh_run_button(self):
        """根据运行状态和定位状态刷新运行按钮。"""
        if self._running:
            self.btn_run_toggle.setText("停止 (F10)")
            self.btn_run_toggle.setStyleSheet(
                "background-color: #f44336; color: white; font-weight: bold; padding: 8px;"
            )
        elif self._target_window is None:
            self.btn_run_toggle.setText("未定位")
            self.btn_run_toggle.setStyleSheet(
                "background-color: #FFC107; color: #333; font-weight: bold; padding: 8px;"
            )
        else:
            self.btn_run_toggle.setText("开始执行 (F9)")
            self.btn_run_toggle.setStyleSheet(
                "background-color: #4CAF50; color: white; font-weight: bold; padding: 8px;"
            )

    # ─── 启停控制 ──────────────────────────────────────────

    def _on_start(self):
        """开始执行"""
        if self._running:
            return
        if self._target_window is None:
            message = '请先扫描窗口并点击"定位"，再开始执行。'
            self.log_text.append(f"[提示] {message}")
            self.statusBar().showMessage("未定位窗口 | 请先扫描窗口并点击定位")
            return
        flow = self.flow_selector.currentText()
        mode = self.mode_selector.currentText()
        self._running = True
        self._stop_requested = False
        self.log_text.append(f"[操作] 开始执行: 流派={flow}, 模式={mode}")
        self._refresh_run_button()
        self.statusBar().showMessage(f"执行中: {flow} - {mode} | F10 停止")
        self._overlay.set_color("green")

    def _on_stop(self):
        """停止执行（转发到统一停止入口）"""
        self._request_stop()

    def _on_toggle_running(self):
        """单按钮切换运行状态。"""
        if self._running:
            self._request_stop()
        else:
            self._on_start()

    # ─── 扫描装备 ──────────────────────────────────────────

    def _on_scan(self):
        """扫描穿戴装备"""
        flow = self.flow_selector.currentText()
        self.log_text.append(f"[操作] 扫描穿戴装备 (流派: {flow})")
        # TODO: Phase 6 实现
        self.log_text.append("[提示] 扫描功能待实现")
