"""运行控制混入类 - 用户/布局选择器、启停控制、毕业率执行"""

from loguru import logger


class RunControlMixin:
    """运行控制混入类

    依赖主类提供:
        _user_manager, _layout_manager, _target_window, _running,
        _capture, _ocr, _input, _overlay,
        user_combo, layout_combo, btn_graduation, btn_run_toggle,
        flow_selector, mode_selector, log_text, statusBar()
    """

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

    # ─── 毕业率按钮 ────────────────────────────────────────

    def _on_graduation(self):
        """执行装备分析流程"""
        if not self._target_window:
            self.log_text.append("[错误] 请先定位窗口")
            self.statusBar().showMessage("未定位窗口 | 请先扫描窗口并点击定位")
            return

        user_name = self._user_manager.get_active_user_name()
        layout_name = self._layout_manager.get_active_layout_name()
        layout = self._layout_manager.load_layout(layout_name)

        if not layout:
            self.log_text.append(f"[错误] 无法加载布局: {layout_name}")
            return

        # 延迟校验：检查所需场景是否已绑定区域
        from ..workflows.equip_analysis import REQUIRED_SCENES
        missing = self._layout_manager.check_scenes_valid(layout_name, REQUIRED_SCENES)
        if missing:
            names = "、".join(missing)
            self.log_text.append(f"[错误] 以下场景未绑定区域: {names}")
            self.statusBar().showMessage(f"场景缺失: {names}")
            return

        from ..workflows.equip_analysis import EquipAnalysisWorkflow
        workflow = EquipAnalysisWorkflow(
            capture=self._capture,
            ocr=self._ocr,
            input_ctrl=self._input,
            layout=layout,
            user_name=user_name,
            window_left=self._target_window["left"],
            window_top=self._target_window["top"],
        )

        self.log_text.append("[开始] 装备分析流程...")
        self.btn_graduation.setEnabled(False)
        try:
            result = workflow.run()
            self.log_text.append(f"[完成] 识别到 {len(result)} 件装备")
        except Exception as e:
            self.log_text.append(f"[错误] 流程执行失败: {e}")
            logger.exception("装备分析流程异常")
        finally:
            self.btn_graduation.setEnabled(True)

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
        self.log_text.append(f"[操作] 开始执行: 流派={flow}, 模式={mode}")
        self._refresh_run_button()
        self.statusBar().showMessage(f"执行中: {flow} - {mode} | F10 停止")
        self._overlay.set_color("green")

    def _on_stop(self):
        """停止执行"""
        if not self._running:
            return
        self._running = False
        self.log_text.append("[操作] 停止执行")
        self._refresh_run_button()
        self.statusBar().showMessage("已停止 | F9 开始")
        self._overlay.set_color("red")

    def _on_toggle_running(self):
        """单按钮切换运行状态。"""
        if self._running:
            self._on_stop()
        else:
            self._on_start()

    # ─── 扫描装备 ──────────────────────────────────────────

    def _on_scan(self):
        """扫描穿戴装备"""
        flow = self.flow_selector.currentText()
        self.log_text.append(f"[操作] 扫描穿戴装备 (流派: {flow})")
        # TODO: Phase 6 实现
        self.log_text.append("[提示] 扫描功能待实现")
