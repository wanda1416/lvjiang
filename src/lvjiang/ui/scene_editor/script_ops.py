"""脚本测试混入类 - DSL 脚本测试器"""

from loguru import logger
from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import (
    QFileDialog,
    QMessageBox,
    QPushButton,
    QTextEdit,
)

from ...core.config.resolver import get_resolver
from ...i18n import tr
from ..button_styles import apply_button_style


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
        super().__init__(tr("输入当前场景"), parent)
        self._get_scene_key = get_scene_key
        self._target: QTextEdit | None = None
        self.setToolTip(tr("点击在脚本编辑器光标处插入当前场景 key"))

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
            self, tr("选择脚本文件"),
            str(get_resolver().write_dir("workflows")),
            tr("工作流文件 (*.wf);;所有文件 (*)"),
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
            self._status_bar.showMessage(tr("脚本内容为空，无法保存"))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, tr("保存脚本文件"),
            str(get_resolver().write_dir("workflows")),
            tr("工作流文件 (*.wf)"),
        )
        if not path:
            return
        from pathlib import Path
        Path(path).write_text(content, encoding="utf-8")
        self._status_bar.showMessage(f"已保存: {path}")
        logger.info(f"已保存脚本: {path}")

    def _owner_main_window(self):
        """宿主主窗口。

        场景管理是独立顶层窗口（脱开父子关系才能被主界面压下去，见
        ``MenuOpsMixin._show_modeless_tool``），所以不能再用 ``self.parent()``；
        打开时注入的 ``_owner_window`` 才是宿主。回退到 parent() 是为了直接
        构造该对话框的测试与其他调用方。
        """
        return getattr(self, "_owner_window", None) or self.parent()

    # ─── 脚本执行 ────────────────────────────────────────

    def showEvent(self, event: QShowEvent | None):  # type: ignore[misc]
        """对话框首次显示时填充用户下拉列表"""
        super().showEvent(event)  # type: ignore[misc]
        main_win = self._owner_main_window()
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

    # ─── 脚本测试：启停 ──────────────────────────────────

    def _script_is_running(self) -> bool:
        worker = getattr(self, "_script_worker", None)
        return worker is not None and worker.isRunning()

    def _on_script_test(self):
        """「运行脚本 / 结束运行」同一个按钮：没在跑就启动，在跑就请求停止。"""
        if self._script_is_running():
            self._request_script_stop()
            return
        self._start_script_test()

    def _request_script_stop(self):
        """请求停止。引擎在语句边界查 stop_check，所以不是立刻返回。

        按下后立即禁用按钮并改文案：停止是异步的，这段时间里重复点击既不会更快
        停下，也会让人以为没响应。
        """
        self._script_stop_requested = True
        self._btn_run_script.setEnabled(False)
        self._btn_run_script.setText(tr("正在结束..."))
        self._status_bar.showMessage(tr("已请求结束脚本，等待当前指令完成..."))

    def _set_script_running_ui(self, running: bool):
        """按钮在"运行脚本"与"结束运行"之间切换"""
        self._btn_run_script.setEnabled(True)
        if running:
            self._btn_run_script.setText(tr("结束运行"))
            apply_button_style(self._btn_run_script, variant="danger")
        else:
            self._btn_run_script.setText(tr("运行脚本"))
            apply_button_style(self._btn_run_script)

    def _confirm_script_stopped_before_close(self) -> bool:
        """关闭前的脚本门禁：还在跑就先请求停止并留住窗口。

        不能直接关：工作线程的 parent 是本对话框，而对话框带 WA_DeleteOnClose，
        关掉就是在线程还在驱动鼠标键盘时销毁它的父对象。
        """
        if not self._script_is_running():
            return True
        self._request_script_stop()
        QMessageBox.information(
            self, tr("脚本仍在运行"),  # type: ignore[arg-type]
            tr("已请求结束脚本测试，等它停下后再关闭窗口。"))
        return False

    def _start_script_test(self):
        """执行脚本测试器中的 DSL 脚本，结果输出到左侧 _result_text"""
        script = self._script_text.toPlainText().strip()
        if not script:
            self._result_text.setPlainText(tr("[错误] 脚本内容为空"))
            return
        # 上一轮的停止标志必须先清掉，否则刚停过一次之后新的一轮会一启动就自杀
        self._script_stop_requested = False

        # 检查是否有宿主窗口（主窗口）提供运行环境
        main_win = self._owner_main_window()
        if main_win is None:
            self._result_text.setPlainText(tr("[错误] 无主窗口，无法获取运行环境"))
            return

        if getattr(main_win, "_running", False):
            self._result_text.setPlainText(
                tr("[错误] 主窗口正在执行任务，请先停止后再运行脚本"))
            return

        backend = getattr(main_win, "_backend", "windows")
        if backend == "adb":
            # ADB 模式：检查设备是否已连接
            if not getattr(main_win, "_device_ready", False):
                self._result_text.setPlainText(tr("[错误] 请先在主窗口连接 ADB 设备"))
                return
            window_left = 0
            window_top = 0
        else:
            # Windows 投屏模式：检查窗口是否已定位
            if not hasattr(main_win, '_target_window') or main_win._target_window is None:
                self._result_text.setPlainText(tr("[错误] 请先在主窗口定位游戏窗口"))
                return
            window_left = main_win._target_window["left"]
            window_top = main_win._target_window["top"]

        self._result_text.clear()
        self._status_bar.showMessage(tr("脚本测试运行中..."))

        # 刷新用户下拉列表（每次运行前刷新，确保包含最新用户）
        self._refresh_script_user_combo(main_win)

        try:  # noqa: PLR1702 — 构建期的失败路径与原实现保持一致
            # 构建 WorkflowEngine
            from ...workflows.engine import DeviceWorkflowEngineBuilder
            layout_key = self._current_layout.key if self._current_layout else ""
            if not layout_key:
                self._result_text.setPlainText(tr("[错误] 没有已加载的布局"))
                return

            layout = main_win._layout_manager.load_layout(layout_key)
            if not layout:
                self._result_text.setPlainText(f"[错误] 无法加载布局: {layout_key}")
                return

            engine = DeviceWorkflowEngineBuilder(
                capture=main_win._capture,
                ocr=main_win._ocr,
                input_ctrl=main_win._input,
                layout=layout,
                input_sim=main_win._user_config.input_sim,
                delay_params=main_win._user_config.delay_params,
                android_apps=main_win._user_config.android_apps,
                android_device=getattr(main_win, "_device", None),
                run_env=main_win._selected_run_env(),
                window_left=window_left,
                window_top=window_top,
                stop_check=lambda: getattr(self, "_script_stop_requested", False),
            ).build()
            # session/context 装配（与主入口一致）
            # 使用下拉列表选中的用户，而非主页面的 active user
            username = self._script_user_combo.currentText()
            if not username:
                # 回退：如果下拉列表为空，使用主页面用户
                username = main_win._user_manager.get_active_user_name()
            engine.session = main_win._session_manager.load(username)
            engine.run_username = username
            # context 由 execute() 自动初始化为空 dict

            # 固定文件保留最近一次运行的草稿，独立临时文件则保证并发执行
            # 期间不会因下一次运行覆盖源码。
            from ...workflows.runtime_source import (
                runtime_source,
                save_editor_snapshot,
            )
            save_editor_snapshot(script)

            # 放进线程执行：引擎里有大量 OCR、等待和长按，同步跑会把编辑器
            # 和主界面一起冻住——卡死期间连"结束运行"都点不到。
            from ..main.run_control import WorkflowWorker

            def _run():
                with runtime_source(script) as temp_wf:
                    result = engine.execute(temp_wf)
                return (result, engine.return_value)

            worker = WorkflowWorker("scene_editor_script", _run, self)
            self._script_worker = worker
            worker.finished.connect(self._on_script_test_finished)
            self._set_script_running_ui(True)
            worker.start()

        except Exception as e:
            import traceback
            self._result_text.setPlainText(f"[错误] {e}\n\n{traceback.format_exc()}")
            self._status_bar.showMessage(tr("脚本测试失败"))
            logger.error(f"脚本测试异常: {e}")
            self._script_worker = None
            self._set_script_running_ui(False)

    def _on_script_test_finished(self):
        """线程结束：格式化结果、恢复按钮。

        无论正常结束、异常还是被停止，都要走到这里把按钮放回"运行脚本"——
        否则按钮会永远停在"结束运行"，而脚本早就不跑了。
        """
        worker = getattr(self, "_script_worker", None)
        self._script_worker = None
        stopped = getattr(self, "_script_stop_requested", False)
        self._script_stop_requested = False
        self._set_script_running_ui(False)
        if worker is None:
            return

        outcome = worker.result_or_exception
        if isinstance(outcome, BaseException):
            import traceback
            detail = "".join(traceback.format_exception(
                type(outcome), outcome, outcome.__traceback__))
            self._result_text.setPlainText(f"[错误] {outcome}\n\n{detail}")
            self._status_bar.showMessage(tr("脚本测试失败"))
            return

        import json

        from ..main.run_control import _to_serializable

        result, return_value = outcome if outcome is not None else (None, None)
        lines = []
        if return_value is not None:
            lines.append(f"返回值：{_format_value(return_value)}")
        else:
            lines.append(tr("返回值：(无)"))
        if result:
            lines.append(tr("结果集："))
            lines.append(json.dumps(
                _to_serializable(result), ensure_ascii=False, indent=2))
        else:
            lines.append(tr("结果集：(空)"))
        self._result_text.setPlainText("\n".join(lines))
        self._status_bar.showMessage(
            tr("脚本已结束运行") if stopped else tr("脚本测试完成"))
