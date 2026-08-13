"""脚本测试混入类 - DSL 脚本测试器"""

from loguru import logger
from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import QFileDialog, QPushButton, QTextEdit

from ...core.config.resolver import get_resolver


def _format_value(value) -> str:
    """格式化单个值为可读字符串"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, (list, dict)):
        import json
        return json.dumps(value, ensure_ascii=False)
    return str(value)


class _SceneKeyButton(QPushButton):
    """场景 key 按钮：点击后在脚本编辑器光标处插入当前场景 key"""

    def __init__(self, get_scene_key, parent=None):
        super().__init__("输入当前场景", parent)
        self._get_scene_key = get_scene_key
        self._target: QTextEdit | None = None
        self.setToolTip("点击在脚本编辑器光标处插入当前场景 key")

    def set_target(self, target: QTextEdit):
        self._target = target

    def _on_clicked(self):
        key = self._get_scene_key()
        if key and self._target is not None:
            cursor = self._target.textCursor()
            cursor.insertText(key)
            self._target.setTextCursor(cursor)
            self._target.setFocus()


class ScriptOpsMixin:
    """脚本测试混入类

    依赖主类提供:
        _script_text, _result_text, _status_bar, _current_layout,
        _get_current_scene_key()
    """

    # ─── 脚本文件 ────────────────────────────────────────

    def _auto_load_script(self):
        """自动加载 _editor_run.wf 到脚本编辑器（local 影子优先）"""
        script_path = get_resolver().resolve_read("workflows/_editor_run.wf")
        if script_path is not None:
            content = script_path.read_text(encoding="utf-8")
            self._script_text.setPlainText(content)
            logger.info(f"已自动加载脚本: {script_path}")

    def _on_load_script_file(self):
        """加载 .wf 文件到脚本编辑器"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择脚本文件",
            str(get_resolver().write_dir("workflows")),
            "工作流文件 (*.wf);;所有文件 (*)",
        )
        if not path:
            return
        from pathlib import Path
        content = Path(path).read_text(encoding="utf-8")
        self._script_text.setPlainText(content)
        logger.info(f"已加载脚本: {path}")

    def _on_save_script_file(self):
        """将脚本编辑器内容保存为 .wf 文件"""
        content = self._script_text.toPlainText().strip()
        if not content:
            self._status_bar.showMessage("脚本内容为空，无法保存")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存脚本文件",
            str(get_resolver().write_dir("workflows")),
            "工作流文件 (*.wf)",
        )
        if not path:
            return
        from pathlib import Path
        Path(path).write_text(content, encoding="utf-8")
        self._status_bar.showMessage(f"已保存: {path}")
        logger.info(f"已保存脚本: {path}")

    # ─── 脚本执行 ────────────────────────────────────────

    def showEvent(self, event: QShowEvent):
        """对话框首次显示时填充用户下拉列表"""
        super().showEvent(event)
        main_win = self.parent()
        if main_win is not None and hasattr(main_win, '_user_manager'):
            self._refresh_script_user_combo(main_win)

    def _refresh_script_user_combo(self, main_win):
        """刷新脚本测试用户下拉列表

        默认选中主页面当前用户；如果用户已手动选择其他用户，保持其选择。
        不改变主页面的 active user。
        """
        if not hasattr(self, '_script_user_combo'):
            return
        users = main_win._user_manager.list_users()
        active = main_win._user_manager.get_active_user_name()
        current = self._script_user_combo.currentText()

        self._script_user_combo.blockSignals(True)
        self._script_user_combo.clear()
        self._script_user_combo.addItems(users)

        # 优先保持用户已选项；否则默认主页面 active user
        if current and current in users:
            idx = self._script_user_combo.findText(current)
        else:
            idx = self._script_user_combo.findText(active)
        if idx >= 0:
            self._script_user_combo.setCurrentIndex(idx)
        self._script_user_combo.blockSignals(False)

    def _on_script_test(self):
        """执行脚本测试器中的 DSL 脚本，结果输出到左侧 _result_text"""
        script = self._script_text.toPlainText().strip()
        if not script:
            self._result_text.setPlainText("[错误] 脚本内容为空")
            return

        # 检查是否有父窗口（主窗口）提供运行环境
        main_win = self.parent()
        if main_win is None:
            self._result_text.setPlainText("[错误] 无主窗口，无法获取运行环境")
            return

        backend = getattr(main_win, "_backend", "windows")
        if backend == "adb":
            # ADB 模式：检查设备是否已连接
            if not getattr(main_win, "_device_ready", False):
                self._result_text.setPlainText("[错误] 请先在主窗口连接 ADB 设备")
                return
            window_left = 0
            window_top = 0
        else:
            # Windows 投屏模式：检查窗口是否已定位
            if not hasattr(main_win, '_target_window') or main_win._target_window is None:
                self._result_text.setPlainText("[错误] 请先在主窗口定位游戏窗口")
                return
            window_left = main_win._target_window["left"]
            window_top = main_win._target_window["top"]

        self._result_text.clear()
        self._status_bar.showMessage("脚本测试运行中...")

        # 刷新用户下拉列表（每次运行前刷新，确保包含最新用户）
        self._refresh_script_user_combo(main_win)

        try:
            # 构建 WorkflowEngine
            from ...workflows.engine import WorkflowEngine
            layout_name = self._current_layout.name if self._current_layout else ""
            if not layout_name:
                self._result_text.setPlainText("[错误] 没有已加载的布局")
                return

            layout = main_win._layout_manager.load_layout(layout_name)
            if not layout:
                self._result_text.setPlainText(f"[错误] 无法加载布局: {layout_name}")
                return

            engine = WorkflowEngine(
                capture=main_win._capture,
                ocr=main_win._ocr,
                input_ctrl=main_win._input,
                layout=layout,
                input_sim=main_win._user_config.input_sim,
                delay_params=main_win._user_config.delay_params,
                window_left=window_left,
                window_top=window_top,
                stop_check=lambda: False,
            )
            # session/context 装配（与主入口一致）
            # 使用下拉列表选中的用户，而非主页面的 active user
            username = self._script_user_combo.currentText()
            if not username:
                # 回退：如果下拉列表为空，使用主页面用户
                username = main_win._user_manager.get_active_user_name()
            engine.session = main_win._session_manager.load(username)
            engine.run_username = username
            # context 由 execute() 自动初始化为空 dict

            # 写入临时 .wf 文件（按模式落可写层）
            temp_wf = get_resolver().write_entity(
                "workflows/_editor_run.wf", script)

            # 同步执行
            result = engine.execute(temp_wf)
            return_value = engine.return_value

            # 格式化输出结果到左侧结果区
            import json

            from ..run_control import _to_serializable

            # 构建显示内容
            lines = []

            # 返回值
            if return_value is not None:
                lines.append(f"返回值：{_format_value(return_value)}")
            else:
                lines.append("返回值：(无)")

            # 结果集
            if result:
                lines.append("结果集：")
                serializable = _to_serializable(result)
                lines.append(json.dumps(serializable, ensure_ascii=False, indent=2))
            else:
                lines.append("结果集：(空)")

            self._result_text.setPlainText("\n".join(lines))
            self._status_bar.showMessage("脚本测试完成")

        except Exception as e:
            import traceback
            self._result_text.setPlainText(f"[错误] {e}\n\n{traceback.format_exc()}")
            self._status_bar.showMessage("脚本测试失败")
            logger.error(f"脚本测试异常: {e}")
