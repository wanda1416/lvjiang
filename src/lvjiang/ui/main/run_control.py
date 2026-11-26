"""运行控制混入类 - 用户/布局选择器、启停控制、工作流通用执行"""

import copy
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from loguru import logger
from PyQt6.QtWidgets import QMessageBox, QWidget

from ...core.fs_util import task_result_path
from ...i18n import tr
from ...workflows.engine import DeviceWorkflowEngineBuilder, WorkflowEngine
from ...workflows.result_protocol import return_failure_message
from ..execution.feedback import (
    apply_idle_start,
    idle_start_state,
    report_start_failure,
    show_running_feedback,
)
from ..widgets.button_styles import apply_execution_button_style
from .execution_access import guarded_finish, guarded_launch
from .run_android_recovery import _AndroidRecoveryMixin
from .run_selectors import _RunSelectorsMixin
from .run_worker import (
    WorkflowWorker,
    _AcknowledgedPauseEvent,
    _prepare_modeless_dialog,
    _show_modeless_dialog,
    _StreamRecheckBridge,
    _to_history_snapshot,
    _to_serializable,
    _UIHelper,
    other_task_running_label,
)
from .run_worker import (
    _PauseMessageBox as _PauseMessageBox,
)

# 顶部上下文选择器的锁定原因。定义在这里而不是 window.py：window 已经
# import 本模块，反向 import 会成环。
LOCK_REASON_BATCH = "batch"
LOCK_REASON_PLAN = "plan"
LOCK_REASON_RUNNING = "running"

# 方案下拉的「不使用方案」项，userData 为空串。
PLAN_CUSTOM_LABEL = "-" + tr('自定义') + " -"

# automation_state_changed 的非常规状态。订阅方必须显式处理——
# 它们的 else 分支都会把未知状态当成「就绪」。
STATE_PLAN_UNSUPPORTED = "plan_unsupported"
#: 目标空闲但并发门禁不允许再起一个任务（当前仅 Lv1 未激活会命中）
STATE_START_DENIED = "start_denied"
STATE_PAUSING = "pausing"
STATE_STOPPING = "stopping"
STATE_WAITING_TARGET = "waiting_target"


class RunControlMixin(_RunSelectorsMixin, _AndroidRecoveryMixin):
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
    #: 空闲重建画面流的工作桥；None 表示当前没有重建在跑。
    _stream_recheck_bridge: _StreamRecheckBridge | None = None
    #: 通道横幅当前归属的目标；None 表示没有显示。
    _adb_banner_target_id: str | None = None
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

    def _sync_projected_run_state(self, run_context, state_value: str) -> None:
        """仅当目标仍是当前查看目标时同步 _run_state 与运行按钮，避免后台目标污染投影。"""
        if run_context is None or run_context is not getattr(self, "_current_run_context", None):
            return
        self._run_state = state_value
        refresh = getattr(self, "_refresh_run_button", None)
        if callable(refresh) and (
            getattr(self, "_user_config", None) is not None
            or getattr(refresh, "__func__", None) is not RunControlMixin._refresh_run_button
        ):
            refresh()

    def _capture_launch_draft(self, target_id: str | None) -> None:
        target = self._execution_targets.get(target_id)
        if target is None or self._running:
            return
        save_params = getattr(self, "_save_displayed_params", None)
        if callable(save_params):
            save_params()
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
        # 草稿可能带着另一个环境（手机 ↔ 端游）。候选集必须先按它重建，
        # 再让参数面板按新脚本绘制，日常下拉、分组、批量页和参数面板才是
        # 同一份事实。上面屏蔽了环境信号，这里不补就只剩参数面板会跟着变。
        self._sync_task_views_to_env(draft.workflow_id or None)
        flow_cfg = self._get_selected_flow_config()
        self._displayed_script_id = flow_cfg["id"] if flow_cfg else None
        for name in ("reference_space_combo", "_env_combo", "layout_combo"):
            setter = getattr(getattr(self, name, None), "set_locked", None)
            if setter is not None:
                setter(LOCK_REASON_PLAN, bool(draft.plan_id))
        self._rebuild_param_panel()
        self._apply_launch_draft_parameters(draft.parameters)

    def _apply_launch_draft_parameters(self, values: dict[str, Any]) -> None:
        """把冻结的运行快照投影到参数面板（只展示，不回写）。"""
        if not values:
            return
        form = getattr(self, "_effective_param_form", None)
        if form is None:
            return
        form.set_values(values)
        self._refresh_param_visibility()
        if self._running and hasattr(self, "_displayed_param_baseline"):
            # 冻结运行快照只是只读展示，结束/退出时不能回写其历史参数。
            self._displayed_param_baseline = copy.deepcopy(form.collect())

    # ─── 自动化状态管理 ────────────────────────────────────

    def _begin_automation(
        self, name: str, *, username: str | None = None,
        execution_scope: str = "daily", allow_portrait: bool = False,
        check_device_busy: bool = True,
    ) -> bool:
        """开始自动化，返回是否成功。若已有自动化在运行则拒绝。"""
        from ...core.control import get_active_control_server

        control_srv = get_active_control_server()
        hk = self._user_config.hotkeys
        if (
            self._running
            or (self._current_worker is not None and self._current_worker.isRunning())
            or (control_srv is not None and control_srv.has_active_job())
        ):
            self.append_log(self._hotkey_status(
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
                self.append_log(
                    tr("[启动失败] {name}：{message}").format(
                        name=target.display_name, message=message))
                report_start_failure(self, message, execution_scope)
                return False
            target.width = int(target.window.get("width") or 0)
            target.height = int(target.window.get("height") or 0)
            self._target_window = target.window
        admin_error = self._plan_admin_requirement_error()
        if admin_error:
            self.statusBar().showMessage(admin_error)
            self._show_workflow_start_error(admin_error, scope=execution_scope)
            return False
        aspect_error = self._layout_aspect_error(
            allow_portrait=(
                allow_portrait and target is not None and target.kind == "adb"))
        if aspect_error:
            self.statusBar().showMessage(aspect_error)
            self._show_workflow_start_error(aspect_error, scope=execution_scope)
            return False
        if target is None:
            self._show_workflow_start_error(tr("当前没有可执行目标"), scope=execution_scope)
            return False
        # 设备执行占用是启动成败的硬前提：手机端还在跑别的任务时，工作流线程
        # 要到 engine.execute → control_begin 才失败。这里在占用本地目标之前
        # 先试取一次（拿到即释放，判据与真正执行同为 control_begin），被占用
        # 就弹窗说明，不留下"一闪就结束 + 一行 ERROR"的体验。
        device_busy = self.device_execution_busy() if check_device_busy else ""
        if device_busy:
            self._notify_device_busy(device_busy)
            report_start_failure(self, device_busy, execution_scope)
            return False
        target_snapshot = target.snapshot()
        from ...core.config import load_user_config
        recovery = load_user_config().android_recovery if target.kind == "adb" else None
        self._capture_launch_draft(target.id)
        run_username = str(
            getattr(self, "_execution_username_snapshot", "") or ""
            if username is None else username)
        decision, run_context = self._run_manager.try_begin(
            target=target_snapshot, username=run_username, name=name)
        if run_context is None:
            self.append_log(f"[拒绝] {decision.reason}")
            self.statusBar().showMessage(decision.reason)
            report_start_failure(self, decision.reason, execution_scope)
            return False
        self._current_run_context = run_context
        if recovery is not None:
            run_context.metadata["android_recovery"] = recovery
        if recovery is not None and target.device is not None:
            target.device.recovery = recovery
            configure = getattr(target.input_ctrl, "configure_recovery", None)
            if configure is not None:
                configure(recovery)
        run_context.metadata["execution_scope"] = execution_scope
        run_context.metadata["launch_draft"] = copy.deepcopy(target.launch_draft)
        run_context.lease = getattr(self, "_execution_lease", None)
        # RapidOCR/ONNX 的同实例并发安全没有契约保证；每个运行实例持有
        # 独立的懒加载引擎，避免多设备推理互相污染。
        from ...core.ocr.engine import OCREngine
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

    def _layout_aspect_error(self, *, allow_portrait: bool = False) -> str:
        """返回画布宽高比不符合布局声明的说明；空串表示通过。

        布局声明了画布尺寸（如 20:9）却对不上，说明目标窗口或设备的画面形态跟这个
        布局压根不是一路的——最典型的是端游窗口模式选了「桌面全屏」，或者设备不是
        20:9。这种错配不会报错，只会让所有坐标整体偏移，所以在启动前拦住。

        ``allow_portrait`` 为真时（仅安卓批量开启），宽高交换后能匹配的竖屏画面
        也放行——客户端还没起来时设备就是竖屏，属于正常的临时形态。

        未声明尺寸、未定位窗口、拿不到截图尺寸时一律放行：门禁只拦已知的错配，
        不拦「还不知道」。
        """
        from ...core.layout.config import (
            canvas_aspect_deviation,
            canvas_aspect_matches,
            parse_aspect_ratio,
        )

        key = self.layout_combo.currentData()
        if not key:
            return ""
        layout = self._layout_manager.load_layout(key)
        if layout is None or not layout.aspect:
            return ""
        capture = getattr(self, "_capture", None)
        if capture is None:
            return ""
        try:
            width, height = capture.get_capture_size()
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"取截图尺寸失败，跳过画布尺寸校验: {exc}")
            return ""
        passed, _ = canvas_aspect_matches(
            layout, float(width), float(height), allow_portrait=allow_portrait)
        if passed:
            return ""
        expected = parse_aspect_ratio(layout.aspect)
        if expected is None:
            return ""
        canvas = layout.canvas
        canvas_w = canvas.w_ratio * float(width)
        canvas_h = canvas.h_ratio * float(height)
        deviation = canvas_aspect_deviation(expected, canvas_w, canvas_h)
        if deviation is None:
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
            for callback in tuple(
                getattr(context.engine, "run_finished_callbacks", None) or ()
            ):
                try:
                    callback(context.task_run_id, final_state.value)
                except Exception:  # noqa: BLE001
                    logger.exception("运行结束回调执行失败: {}", context.task_run_id)
            self._emit_run_instance_state(context, final_state.value)
            self._run_manager.finish(
                context.task_run_id, final_state,
            )
            self._emit_concurrency_changed()
        if context is not None:
            hide_banner = getattr(self, "_hide_channel_banner", None)
            if callable(hide_banner):
                hide_banner(context.target_id)
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
        if context is None:
            drop_banner = getattr(self, "_drop_channel_banner", None)
            if callable(drop_banner):
                drop_banner()
            else:
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

    def _show_workflow_start_error(self, message: str, *, scope: str = "daily"):
        """报告启动前错误，但不因提示窗口禁用主界面。"""
        from PyQt6.QtWidgets import QMessageBox, QWidget

        self.append_log(f"[错误] {message}")
        report_start_failure(self, message, scope)
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
        task_stop_callback = None
        if context is not None and context.metadata.get("batch"):
            task_stop_callback = context.task_stop_event.set
        helper = _UIHelper(
            self, stop_check=stop_check,
            task_stop_callback=task_stop_callback,
        )
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
            self.append_log(tr("[提示] 当前没有正在运行的自动化"))
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
        self.append_log(tr("[操作] 收到停止请求"))
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
        if run_context is not None:
            hide_banner = getattr(self, "_hide_channel_banner", None)
            if callable(hide_banner):
                hide_banner(run_context.target_id)
        else:
            drop_banner = getattr(self, "_drop_channel_banner", None)
            if callable(drop_banner):
                drop_banner()
            else:
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
            self.append_log(tr("[操作] 已停止"))

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
        self.append_log(f"{tr('[操作] ')}{paused_status}")
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
        self.append_log(f"{tr('[操作] ')}{paused_status}")
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
        self.append_log(tr("[操作] 已恢复，继续执行..."))
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

    def device_execution_busy(self) -> str:
        """设备当前能否立刻交出执行占用；空闲返回空串，被占用返回设备给出的原因。

        设备端的执行占用原本只在每个条目准备时获取，手机端正在跑任务时整批会逐条
        失败并重复同一句原因。这里先取一次再立刻释放，判断依据与真正执行时完全一致
        ——同样是 `control_begin`，所以不会出现“预检放行、真跑被拒”的口径差异。
        窗口模式、纯 ADB 输入等没有该能力的后端直接放行，交给原流程处理。
        """
        target = self._current_execution_target()
        if target is None or getattr(target, "kind", "") != "adb":
            return ""
        input_ctrl = getattr(target, "input_ctrl", None)
        probe = getattr(input_ctrl, "probe_execution_busy", None)
        if callable(probe):
            try:
                return str(probe() or "")
            except Exception as exc:  # 传输类故障交给真正的执行路径报，预检不额外拦截
                logger.warning(f"设备执行占用预检失败，按原流程继续: {exc}")
                return ""
        begin = getattr(input_ctrl, "begin_execution", None)
        end = getattr(input_ctrl, "end_execution", None)
        if not callable(begin) or not callable(end):
            return ""
        from ...core.android.agent import AgentOpError
        try:
            begin()
        except AgentOpError as exc:
            return str(exc)
        except Exception as exc:  # 传输类故障交给真正的执行路径报，预检不额外拦截
            logger.warning(f"设备执行占用预检失败，按原流程继续: {exc}")
            return ""
        try:
            end()
        except Exception as exc:
            logger.warning(f"设备执行占用预检释放失败: {exc}")
        return ""

    def _notify_device_busy(self, reason: str) -> None:
        """设备执行占用被别的任务持有时，启动前弹窗说明原因。

        以前这条冲突要等工作流线程跑到 ``engine.execute → control_begin`` 才暴
        露，用户看到的是"点下去一闪就结束 + 一行 ERROR 日志"，既不知道原因，
        也不知道该去哪儿结束占用。占用检查提前到启动前，用弹窗讲清楚。
        """
        message = tr("设备正在执行其他任务，本次未启动。\n\n{reason}").format(
            reason=reason)
        self.append_log(f"[{tr('提示')}] {reason}")
        self.statusBar().showMessage(tr("设备正在执行其他任务"))
        QMessageBox.warning(cast(QWidget, self), tr("无法开始执行"), message)

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
        self.append_log(f"[{tr('错误')}] {message}")
        self.statusBar().showMessage(message)

    # ─── 通用工作流执行 ────────────────────────────────────

    @guarded_launch(user_selector="_daily_execution_user_selector")
    def _on_run_workflow(self, *, flow_config=None, execution_username=None, parameter_overrides=None):
        """执行选中的工作流（异步）；运行中点击则作为停止按钮。"""
        # 运行中时该按钮文字为“停止 (F10)”，点击应触发停止而非重复启动
        if self._running:
            self._request_stop()
            return

        self._save_displayed_params()

        feedback = getattr(self, "_daily_start_feedback", None)
        if feedback is not None:
            feedback.clear_failure()

        if not self._backend_ready():
            target = self._current_execution_target()
            if target is None:
                self.append_log(tr("[错误] 请先连接并选择执行目标"))
                self.statusBar().showMessage(tr("未连接 | 请先定位窗口或连接设备"))
            elif self._backend == "adb":
                self.append_log(tr("[错误] 请先连接设备"))
                self.statusBar().showMessage(tr("未连接设备 | 请先扫描并连接设备"))
            else:
                self.append_log(tr("[错误] 请先定位窗口"))
                self.statusBar().showMessage(tr("未定位窗口 | 请先扫描窗口并点击定位"))
            return

        # 连接方式确定之后才谈得上方案支不支持。
        if not self._plan_allows_backend():
            self._notify_plan_unsupported()
            return

        flow_cfg = flow_config if flow_config is not None else self._get_selected_flow_config()
        if flow_cfg is None:
            self.append_log(tr("[错误] 请选择一个工作流"))
            if feedback is not None:
                feedback.show_failure(tr("请选择一个脚本"))
            return

        # env 限制检查：脚本声明了 env 且当前环境不在列表中，阻止执行
        current_env = self._selected_run_env()
        env_list = flow_cfg.get("env") or []
        if env_list:
            if current_env not in env_list:
                message = tr("当前工作环境 {env} 不在该脚本支持的环境 {envs} 中").format(
                    env=current_env, envs=", ".join(env_list))
                self.append_log(f"[错误] {message}")
                if feedback is not None:
                    feedback.show_failure(message)
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

        username = execution_username or self._execution_username_snapshot
        if not username:
            self.append_log(tr("[错误] 请选择有效的执行用户"))
            if feedback is not None:
                feedback.show_failure(tr("请选择有效的执行用户"))
            return

        if not self._begin_automation(flow_name, username=username):
            return

        run_context = self._current_run_context
        assert run_context is not None
        stop_check = run_context.stop_event.is_set

        layout_name = self.layout_combo.currentData()
        layout = self._layout_manager.load_layout(layout_name)

        if not layout:
            self.append_log(f"[错误] 无法加载布局: {layout_name}")
            self._end_automation(flow_name)
            report_start_failure(self, tr("无法加载布局：{name}").format(name=layout_name))
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
        from ...core.config.wf_configs import TRANSIENT_ID_PREFIX, get_wf_config
        temporary = flow_id.startswith(TRANSIENT_ID_PREFIX)
        engine.workflow_config_snapshot = (copy.deepcopy(flow_cfg.get("_saved_params", {}))
                                           if temporary else get_wf_config(flow_id))
        engine._ui_callback = self._create_ui_callback(run_context)
        engine.window_rebind_hook = (
            lambda window, target_id=run_context.target_id:
            self._on_target_window_rebound(target_id, window)
        )
        # 保存 engine 引用供完成回调使用
        self._current_engine = engine
        # 面板已在启动前保存；此处只构造该用户的执行快照。
        if flow_cfg.get("scope", "daily") == "daily":
            from ...core.task_params import merge_task_params, resolve_task_params
            if temporary:
                flow_params, _parameter_source = merge_task_params(
                    flow_cfg.get("parameters", []), flow_cfg.get("_saved_params", {}), None)
            else:
                flow_params, _parameter_source = resolve_task_params(
                    flow_id, username, flow_cfg.get("parameters", []),
                    self._session_manager._users_dir,
                )
        else:
            # 专用脚本的参数面板由日常页隐藏，仍由专属配置页管理。
            from ...core.config.wf_configs import get_wf_config
            flow_params = get_wf_config(flow_cfg["id"]) or {}
        if parameter_overrides is not None:
            flow_params.update(parameter_overrides)
        if flow_config is None and hasattr(self, '_save_daily_config'):
            self._save_daily_config()

        self.append_log(f"[开始] {flow_name} 流程...")
        self.append_log(f"[执行用户] {username}")
        if flow_params:
            self.append_log(f"[参数] {flow_params}")

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
            from ...core.history.daily import try_create_task_run
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
        diagnostics = getattr(self, "_task_diagnostics", None)
        request = diagnostics.freeze() if diagnostics is not None else None
        if request is not None:
            engine = getattr(self, "_current_engine", None)
            if engine is not None:
                engine.execution_scope = request.scope(
                    task_run_id=run_context.task_run_id, target_scope=run_context.target_id,
                    previous=engine.execution_scope)
            else:
                logger.warning("任务诊断已开启，但当前没有可用的执行引擎；本次不记录最近帧")
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
            self.append_log(f"[错误] {flow_name}流程异常退出: {result_or_exception}")
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
            # 顶层 return 按通用返回协议判定失败（{"status": "failed"} 或负数）；
            # 用户停止仍按中断处理。归一化结果写回 worker，MCP 等消费方读到同一结论。
            failure = (None if run_context.stop_event.is_set() else
                       return_failure_message(
                           getattr(run_context.engine, "return_value", None)))
            if failure is not None and not (
                    isinstance(result, dict) and result.get("error")):
                result = ({**result, "error": failure} if isinstance(result, dict)
                          else {"error": failure, "result": result})
                worker.result_or_exception = result
            # 预检拒绝（{"error": ...}）与脚本返回失败：都不按正常完成处理
            if isinstance(result, dict) and result.get("error"):
                run_context.metadata["terminal_state"] = "failed"
                self.append_log(f"[错误] {flow_name}: {result['error']}")
                logger.error(f"工作流 {flow_id} 失败: {result['error']}")
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
                self.append_log(f"[已停止] {flow_name}流程被用户中断")
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
            if not interrupted:
                self.append_log(f"[完成] {flow_name} 结果已保存")

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
        """保存工作流结果到 logs/output/{username}/YYYY-MM/DD/。

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
            save_path = task_result_path(
                username, flow_id, started_at,
                task_run_id=task_run_id, interrupted=interrupted,
            )
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text(
                json.dumps(serializable, ensure_ascii=False, indent=2),
                encoding="utf-8",
                newline="\n",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"工作流结果保存失败，继续任务收尾: {exc}")
            self.append_log(f"[警告] {flow_id} 结果 JSON 保存失败: {exc}")
            return None
        logger.info(f"工作流结果已保存: {save_path}")
        self.append_log(f"[保存] {flow_id} → logs/output/{save_path.relative_to(OUTPUT_DIR)}")
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
        self.append_log(f"[{tr('拒绝')}] {decision.reason}")
        self.statusBar().showMessage(decision.reason)

    def _daily_start_reason(self) -> str:
        if hasattr(self, "_workflow_configs") and self._get_selected_flow_config() is None:
            return tr("请选择一个脚本；列表为空时可刷新脚本或调整展示分组")
        selector = getattr(self, "_daily_execution_user_selector", None)
        if selector is not None and not selector.resolve_username():
            return tr("请选择有效的执行用户")
        return ""

    def _refresh_daily_start_feedback(self):
        if self._running or other_task_running_label(self, "daily"):
            self.btn_run_workflow.setToolTip("")
            return
        feedback = getattr(self, "_daily_start_feedback", None)
        if feedback is not None:
            feedback.clear_failure()
        apply_idle_start(self.btn_run_workflow, feedback, self, idle_start_state(self),
                         self._hotkey_label(tr("开始执行"), self._user_config.hotkeys.start),
                         self._daily_start_reason())

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
        else:
            state = idle_start_state(self)
            apply_idle_start(self.btn_run_workflow, getattr(self, "_daily_start_feedback", None),
                             self, state, self._hotkey_label(tr("开始执行"), hk.start),
                             RunControlMixin._daily_start_reason(self))
        if self._running or other_task_running_label(self, "daily"):
            show_running_feedback(getattr(self, "_daily_start_feedback", None),
                                  other_task_running_label(self, "daily"))
            self.btn_run_workflow.setToolTip("")
        self._refresh_pause_button()
        self.automation_state_changed.emit(state)
        self._emit_concurrency_changed()
        # 批量锁原先只在批量启动/结束时直接改主窗口控件。多目标后这些
        # 控件是“当前查看目标”的投影：A 正在跑批量时切到空闲的 B，A 的
        # 锁不能继续残留在 B 上；切回 A 时又必须恢复。运行锁本来就是按
        # 当前目标计算的，批量锁也在同一个刷新点按当前 RunContext 重算。
        self._sync_projected_context_locks()
        # 运行状态变化统一从这里过（开始/暂停/恢复/结束），连接信息列的颜色
        # 跟着这里刷新，不必在每个状态迁移点各写一遍。宿主可能只带部分混入
        # （测试替身、插件页），缺这个方法时跳过。
        apply_colors = getattr(self, "_apply_execution_target_colors", None)
        if callable(apply_colors):
            apply_colors()
        for combo_name in ("plan_combo", "reference_space_combo", "_env_combo",
                           "layout_combo"):
            combo = getattr(self, combo_name, None)
            setter = getattr(combo, "set_locked", None)
            if setter is not None:
                setter(LOCK_REASON_RUNNING, self._running)
        for control_name in ("workflow_combo", "workflow_group_combo",
                             "btn_refresh_workflows", "btn_load_workflow"):
            control = getattr(self, control_name, None)
            if control is not None:
                control.setEnabled(not self._running)
        param_panel = getattr(self, "_param_panel", None)
        if param_panel is not None:
            param_panel.setEnabled(not self._running)
        from ..execution.user_selector import ExecutionUserSelector
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
            self.append_log(tr("[错误] 请选择有效的执行用户"))
            report_start_failure(self, tr("请选择有效的执行用户"), impl_name)
            return

        if not self._backend_ready():
            if self._current_execution_target() is None:
                self.append_log(tr("[错误] 请先连接并选择执行目标"))
            elif self._backend == "adb":
                self.append_log(tr("[错误] 请先连接设备"))
            else:
                self.append_log(tr("[错误] 请先定位窗口"))
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
            self.append_log(f"[错误] 无法加载布局: {layout_name}")
            self._end_automation(flow_name)
            report_start_failure(self, tr("无法加载布局：{name}").format(name=layout_name), impl_name)
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
        from ...core.users.document import load_user_metadata
        user = load_user_metadata(username, self._session_manager._users_dir)
        engine.user_attributes_snapshot = {
            username: dict(user.attributes) if user is not None else {}
        }
        # context 由 execute() 自动初始化为空 dict。
        engine._save_callback = self._session_manager.save_fn(
            username, engine.session)
