"""主窗口运行控制的 Android 执行通道恢复逻辑。"""

import threading
from typing import Any, cast

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox, QWidget

from ...i18n import tr
from .run_worker import _StreamRecheckBridge

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


class _AndroidRecoveryMixin:
    _adb_banner_target_id: str | None
    _stream_recheck_bridge: _StreamRecheckBridge | None

    # ─── Android 执行通道恢复 ────────────────────────────

    def _on_adb_connection_lost(self, target_id: str, error_msg: str, device=None):
        """某台 ADB 设备断连；仅运行目标可以打断当前任务。

        `device` 是发起通知的那条连接。重连替换目标后旧设备仍被在飞的执行链
        持有，它再断线说明的是那条链还没好，而不是目标不可用；目标就绪态只由
        当前连接自己改，否则 `ready` 会被误清掉，开始／批量启动都会被挡在
        「请先连接设备」，而目标行本身还有运行状态（等待重连）可看。
        """
        target = self._execution_targets.get(target_id)
        if target is None:
            return
        if device is None or device is target.device:
            coordinator = getattr(getattr(target, "device", None), "recovery_coordinator", None)
            # 「等待画面流恢复」时设备本身是好的，不能标成离线——
            # 否则开始/批量启动会被"请先连接设备"挡住。
            target.status = "connected" if getattr(coordinator, "waiting_kind", "") in {
                "uncertain_action", "stream"} else "offline"
        self._refresh_execution_targets_ui()
        run_context = self._run_manager.run_for_target(target_id)
        if run_context is None:
            if target_id == self._execution_targets.active_target_id:
                self._sync_active_target_compat()
                self._refresh_run_button()
            self.append_log(
                f"[警告] 非运行目标 {target.display_name} 已离线: {error_msg}")
            return
        from .execution_runs import RunState
        self._run_manager.set_state(
            run_context.task_run_id, RunState.WAITING_TARGET)
        self._emit_run_instance_state(
            run_context, RunState.WAITING_TARGET.value)
        message = tr(
            "[警告] 执行通道等待确认，请处理 {name} 的问题后点击重新检查；"
            "若无法恢复，请按 F10 停止任务后重新连接: {error}").format(
                name=target.display_name, error=error_msg)
        self.append_log(message)
        self._show_connection_recovery_question(target, run_context, error_msg)
        if target_id != self._execution_targets.active_target_id:
            return
        self.statusBar().showMessage(tr(
            "设备执行通道等待确认，请处理问题后重新检查；无法恢复时请停止任务"))
        banner = getattr(self, '_adb_banner', None)
        if banner is not None:
            label = getattr(self, '_adb_banner_label', None)
            if label is not None:
                label.setText(tr(
                    "⚠ 执行通道等待确认：处理问题后点击「重新检查」；"
                    "无法恢复时请按 F10 停止任务"))
            btn = getattr(self, '_adb_banner_btn', None)
            if btn is not None:
                try:
                    btn.clicked.disconnect()
                except TypeError:
                    pass
                btn.clicked.connect(self._resume_adb)
            banner.setVisible(True)

    def _on_stream_lost(self, target_id: str, kind: str, detail: str, device=None):
        """画面流故障（ADB 通道还在）：运行中的任务停下等恢复，空闲时只提示。

        这条路径和 ADB 断连分开：断连要重连 transport，流故障只需要重建 scrcpy
        会话。设备本身是好的，所以目标状态不能标成离线。
        """
        target = self._execution_targets.get(target_id)
        if target is None:
            return
        self._abort_recording_for_target(target_id, tr("画面流中断"))
        self.append_log(tr("[警告] {name} 画面流中断：{reason}").format(
            name=target.display_name, reason=detail))
        if target_id == self._execution_targets.active_target_id:
            label = getattr(self, "preview_label", None)
            if label is not None and label.isVisible():
                label.setText(tr("画面已中断，正在恢复…"))
        run_context = self._run_manager.run_for_target(target_id)
        if run_context is None:
            # 空闲时段不弹窗：挂机时不该被反复打扰，横幅与日志足够提示
            self._show_channel_banner(
                target_id,
                tr("⚠ 画面流已中断：点「重新检查」重试，或断开重连设备"))
            return
        from .execution_runs import RunState
        self._run_manager.set_state(
            run_context.task_run_id, RunState.WAITING_TARGET)
        self._emit_run_instance_state(
            run_context, RunState.WAITING_TARGET.value)
        self._show_connection_recovery_question(
            target, run_context, detail, kind="stream")
        if target_id != self._execution_targets.active_target_id:
            return
        self.statusBar().showMessage(tr("画面流中断，任务正在等待恢复"))
        self._show_channel_banner(
            target_id,
            tr("⚠ 画面流中断：处理后点「重新检查」继续；无法恢复请按 F10 停止任务"))

    def _channel_banner_texts(self) -> dict[str, str]:
        """每条横幅的文案按目标保存。

        切到别的目标时不能继续显示这台设备的提示，切回来又得能恢复——所以文案
        必须按目标记住，而不是只留最后一次设置的那一条。
        """
        texts = getattr(self, "_channel_banner_text_by_target", None)
        if texts is None:
            texts = {}
            self._channel_banner_text_by_target = texts
        return texts

    def _show_channel_banner(self, target_id: str, text: str) -> None:
        """记录并显示通道横幅；非当前查看目标只记录，等切回来再显示。"""
        self._channel_banner_texts()[target_id] = text
        if target_id != self._execution_targets.active_target_id:
            return
        banner = getattr(self, "_adb_banner", None)
        if banner is None:
            return
        label = getattr(self, "_adb_banner_label", None)
        if label is not None:
            label.setText(text)
        btn = getattr(self, "_adb_banner_btn", None)
        if btn is not None:
            try:
                btn.clicked.disconnect()
            except TypeError:
                pass
            btn.clicked.connect(self._resume_adb)
        self._adb_banner_target_id = target_id
        banner.setVisible(True)

    def _hide_channel_banner(self, target_id: str) -> None:
        """收起某目标的横幅。

        按**归属**判断而不是「它此刻是不是当前目标」：目标被移除后
        ``active_target_id`` 会变，按 active 判断会把这些横幅变成谁也收不掉的
        无主残留（断开连接后横幅一直挂着就是这个原因）。
        """
        self._channel_banner_texts().pop(target_id, None)
        if self._adb_banner_target_id != target_id:
            return
        self._drop_channel_banner()

    def _drop_channel_banner(self) -> None:
        """无条件收起横幅：目标已断开/被移除，没有可归属的对象了。"""
        self._adb_banner_target_id = None
        banner = getattr(self, "_adb_banner", None)
        if banner is not None:
            banner.setVisible(False)

    def _sync_channel_banner(self) -> None:
        """按当前查看目标重新应用横幅。

        横幅显示时要求「它就是当前目标」，但切换目标并不重新评估横幅，
        于是看 B 目标时会继续挂着 A 的「画面流已中断」。切目标统一走这里。
        """
        target_id = self._execution_targets.active_target_id
        text = self._channel_banner_texts().get(target_id) if target_id else None
        if text is None:
            self._drop_channel_banner()
            return
        self._show_channel_banner(target_id, text)

    def _show_connection_recovery_question(self, target, run_context, error_msg,
                                           *, kind: str = "connection"):
        """设备恢复属于安全交互，不走无人值守工作流弹窗自动处理。"""
        from PyQt6.QtCore import QTimer

        existing: dict[str, Any] | None = getattr(
            self, '_connection_recovery_dialogs', None)
        dialogs: dict[str, Any] = existing if existing is not None else {}
        if existing is None:
            self._connection_recovery_dialogs = dialogs
        if kind == "stream":
            text = tr(
                "{name} 的画面流中断，自动恢复没有成功。\n{error}\n\n"
                "请检查手机（是否息屏、游戏是否被切走、USB／无线是否正常）后"
                "选择是重新检查；选择否停止该目标任务。"
                "关掉本提示不会停止任务，顶部横幅会一直保留。"
            ).format(name=target.display_name, error=error_msg)
            title = tr("等待画面恢复")
        else:
            text = tr(
                "{name} 的连接恢复未完成。\n{error}\n\n"
                "请恢复 ADB／手机代理连接后选择是重新检查；选择否停止该目标任务。"
            ).format(name=target.display_name, error=error_msg)
            title = tr("等待设备恢复")
        if target.id in dialogs:
            dialogs[target.id].setText(text)
            return
        box = QMessageBox(QMessageBox.Icon.Warning, title, text,
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                          cast(QWidget, self))
        box.setDefaultButton(QMessageBox.StandardButton.No)
        box.setWindowModality(Qt.WindowModality.NonModal)
        dialogs[target.id] = box
        timer = QTimer(box)
        timer.setInterval(200)

        def current():
            return self._run_manager.run_for_target(target.id) is run_context

        def finish(answer):
            timer.stop()
            dialogs.pop(target.id, None)
            if current() and not run_context.stop_event.is_set():
                from .execution_runs import RunState
                if answer == QMessageBox.StandardButton.Yes:
                    if target.resume_event is not None:
                        target.resume_event.set()
                    self.append_log(tr("[操作] 重新检查执行通道"))
                elif answer == QMessageBox.StandardButton.No:
                    run_context.stop_event.set()
                    if run_context.pause_event is not None:
                        run_context.pause_event.set()
                    if target.resume_event is not None:
                        target.resume_event.set()
                    self._run_manager.set_state(run_context.task_run_id, RunState.STOPPING)
                    self._emit_run_instance_state(run_context, RunState.STOPPING.value)
                    self.append_log(tr("[操作] 停止等待恢复的任务"))
                else:
                    # 关窗（X / Esc）只是收起提示：恢复等待没有超时，
                    # 关掉弹窗不能被当成"用户选择停止"。
                    self.append_log(tr(
                        "[提示] 已收起恢复提示，任务仍在等待通道恢复；"
                        "可点顶部横幅「重新检查」或按 F10 停止"))
            box.deleteLater()

        def poll():
            if not current() or run_context.stop_event.is_set():
                box.done(QMessageBox.StandardButton.No)

        box.finished.connect(finish)
        timer.timeout.connect(poll)
        timer.start()
        box.show()

    def _on_adb_connection_restored(self, target_id: str):
        from .execution_runs import RunState
        target = self._execution_targets.get(target_id)
        if target is None:
            return
        target.status = "connected"
        target.input_kind = str(getattr(target.input_ctrl, "kind", "") or "")
        run_context = self._run_manager.run_for_target(target_id)
        if run_context is not None and run_context.state == RunState.WAITING_TARGET:
            if not run_context.stop_event.is_set():
                state = (RunState.PAUSED if run_context.pause_event is not None
                         and not run_context.pause_event.is_set() else RunState.RUNNING)
                self._run_manager.set_state(run_context.task_run_id, state)
                self._emit_run_instance_state(run_context, state.value)
        self._refresh_execution_targets_ui()
        self._hide_channel_banner(target_id)

    def _has_run_waiter(self) -> bool:
        """是否真有一条命令阻塞在恢复等待里。

        ``waiting`` 只在 ``AndroidRecovery.wait_for_user`` 期间为真。任务已经结束
        但 ``_current_run_context`` 还留着旧引用时它为假——此时点「重新检查」只是
        往一个没人等的 Event 上 set 一次，横幅不会动。横幅的空闲分支因此必须能
        分辨「有人在等」和「只是提示」，否则那个按钮点了等于没点。
        """
        target = self._execution_targets.get(self._execution_targets.active_target_id)
        target_dev = getattr(target, "device", None)
        live_fn = getattr(self, "_live_target_devices", None)
        target_id = getattr(target, "id", None)
        devices = (
            live_fn(target_id, extra=target_dev)
            if target_id and callable(live_fn)
            else ([target_dev] if target_dev is not None else [])
        )
        coordinators = [
            coord
            for dev in devices
            if (coord := getattr(dev, "recovery_coordinator", None)) is not None
        ]
        if coordinators:
            return any(bool(getattr(coord, "waiting", False)) for coord in coordinators)
        # 没有恢复协调器（窗口目标、测试替身）：退回按运行实例状态判断。
        from .execution_runs import RunState
        context = getattr(self, "_current_run_context", None)
        return getattr(context, "state", None) == RunState.WAITING_TARGET

    def _resume_adb(self):
        """响应横幅上的「重新检查」。

        有人在等恢复时唤醒它继续原来的重试链；空闲时没有任何等待者，横幅只是
        一条提示，改为**直接重建画面流**——否则这个按钮点了没有任何效果，横幅
        会一直挂在那里直到重启应用。
        """
        # 横幅只为当前查看目标显示，因此唤醒的也必须是该目标的运行实例
        run_context = getattr(self, "_current_run_context", None)
        if run_context is not None:
            dialogs = getattr(self, '_connection_recovery_dialogs', {})
            box = dialogs.get(run_context.target_id)
            if box is not None:
                from PyQt6.QtWidgets import QMessageBox
                box.done(QMessageBox.StandardButton.Yes)
                return
        resume_event = self._run_resume_event(run_context)
        if resume_event is not None and self._has_run_waiter():
            resume_event.set()
            self.statusBar().showMessage(tr("正在重新检查设备执行通道..."))
            self.append_log(tr("[操作] 重新检查设备执行通道"))
            return
        self._recheck_active_stream()

    def _recheck_active_stream(self) -> bool:
        """空闲时重建当前目标的画面流；返回是否已接管这次点击。

        横幅在空闲状态下也会显示（避免启动任务后才发现断流），所以必须给它一条
        真的能消掉的路：``restart()`` 会重建整个 scrcpy 会话并清故障标记，成功
        即收起横幅，失败则把原因写回横幅，允许再试。

        ``restart()`` 是阻塞调用（adb push / forward / 等协议头与首帧），必须放到
        工作线程，否则点一下按钮界面就冻住。
        """
        from ...core.android import AndroidStreamCapture

        target_id = self._execution_targets.active_target_id
        target = self._execution_targets.get(target_id) if target_id else None
        if target is None or target.handle is None:
            return False
        capture = target.handle.binding().capture
        if not isinstance(capture, AndroidStreamCapture):
            # 单帧截图没有流可重建；横幅只可能来自上一次 scrcpy 会话，直接收掉。
            self._hide_channel_banner(target_id)
            self.append_log(tr("[操作] 当前目标是单帧截图，没有可重建的画面流，已收起提示"))
            return True
        if getattr(self, "_stream_recheck_bridge", None) is not None:
            self.append_log(tr("[画面流] 正在重建中，请稍候"))
            return True

        bridge = _StreamRecheckBridge(target_id)
        bridge.finished.connect(self._on_stream_recheck_finished)
        self._stream_recheck_bridge = bridge
        self.statusBar().showMessage(tr("正在重建画面流..."))
        self.append_log(tr("[操作] 重新检查画面流"))

        def work() -> None:
            ok = False
            detail = ""
            try:
                ok = bool(capture.restart())
                if not ok:
                    detail = str(
                        getattr(capture, "last_error", "")
                        or getattr(capture, "last_fault_detail", "") or "")
            except Exception as exc:  # noqa: BLE001 重建失败要回落到横幅，不能炸线程
                detail = str(exc)
            bridge.finished.emit(target_id, ok, detail)

        threading.Thread(target=work, name="stream-recheck", daemon=True).start()
        return True

    def _on_stream_recheck_finished(self, target_id: str, ok: bool, detail: str) -> None:
        """空闲重建结果回到主线程：成功收横幅，失败把原因留在横幅上允许再试。"""
        self._stream_recheck_bridge = None
        if self._execution_targets.get(target_id) is None:
            return
        if ok:
            self.append_log(tr("[画面流] 已恢复"))
            self.statusBar().showMessage(tr("画面流已恢复"))
            self._hide_channel_banner(target_id)
            return
        reason = detail or tr("未知原因")
        self.append_log(tr("[警告] 画面流重建失败：{reason}").format(reason=reason))
        self.statusBar().showMessage(tr("画面流仍未恢复"))
        self._show_channel_banner(
            target_id,
            tr("⚠ 画面流仍不可用：{reason}；可再次点「重新检查」或断开重连设备")
            .format(reason=reason))

