"""窗口操作混入类 - 窗口扫描、定位、截屏、DPI 检测"""

import ctypes
from ctypes import wintypes

import numpy as np
from loguru import logger
from PyQt6.QtCore import QObject, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QMenu, QTreeWidgetItem

from ...i18n import tr
from ..mobile.device_scan import DeviceScanPanel


def bg_capture_tip() -> str:
    """「后台截图」的悬停提示（创建控件与刷新可用性共用一份口径）

    显式标明这是实验能力并且不受理不可用反馈：WGC 取帧路径和一部分脚本的识别/时序
    假设还没磨合好，与其让用户把「脚本跑不通」当成 bug 报回来，不如在勾选前就说清。
    """
    return tr(
        "实验性功能：很多脚本暂时无法在该模式下跑通，不受理该功能的不可用反馈。\n"
        "用 Windows Graphics Capture 取帧，游戏窗口被别的窗口盖住也能截图；"
        "窗口最小化时仍然拿不到画面。仅本次运行期间生效")


def _find_auto_connect_window_index(
        windows: list[dict], title_keyword: str) -> int | None:
    """返回标题自动连接候选；其他律匠窗口只能手动选择。"""
    if not title_keyword:
        return None
    for index, window in enumerate(windows):
        title = str(window.get("title") or "")
        if "律匠" in title:
            continue
        if title_keyword in title:
            return index
    return None


class _AdbConnSignalBridge(QObject):
    """工作流线程 → 主线程的 ADB 断连信号桥"""
    adb_lost = pyqtSignal(str, str)

    def __init__(self, target_id: str):
        super().__init__()
        self._target_id = target_id

    def notify_lost(self, error_msg: str):
        self.adb_lost.emit(self._target_id, error_msg)


class _DeviceWorker(QObject):
    """后台线程：扫描或连接 ADB 设备（互斥，不会同时运行）"""
    scan_finished = pyqtSignal(list)  # devices
    wireless_finished = pyqtSignal(list)  # devices (无线扫描结果)
    wireless_progress = pyqtSignal(str, int, int)  # message, current, total
    connect_finished = pyqtSignal(object, object, str, int, int, object)  # device, capture, method, w, h, agent
    notice = pyqtSignal(str)  # 连接过程中的提示（主线程写进日志区）
    error = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, task: str, serial: str = "", capture_method: str = "",
                 agent_mode: bool = False, subnets: list | None = None):
        super().__init__()
        self._task = task
        self._serial = serial
        self._capture_method = capture_method
        self._agent_mode = agent_mode
        self._subnets = subnets  # None = 扫描全部网卡
        self._cancelled = False

    def cancel(self):
        """取消后台任务"""
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled

    def run(self):
        try:
            if self._task == "scan":
                self._do_scan()
            elif self._task == "wireless_scan":
                self._do_wireless_scan()
            elif self._task == "local_scan":
                self._do_local_scan()
            else:
                self._do_connect()
        except Exception as e:
            if not self._cancelled:
                self.error.emit(str(e))
        finally:
            self.finished.emit()

    def _do_scan(self):
        from ...core.android import list_adb_devices
        devices = list_adb_devices(cancel_check=self.is_cancelled)
        if not self._cancelled:
            self.scan_finished.emit(devices)

    def _do_wireless_scan(self):
        from ...core.android import scan_and_connect_wireless

        def progress_cb(message: str, current: int, total: int):
            self.wireless_progress.emit(message, current, total)

        devices = scan_and_connect_wireless(
            progress_cb=progress_cb,
            subnets=self._subnets,
            cancel_check=self.is_cancelled,
        )
        if not self._cancelled:
            self.wireless_finished.emit(devices)

    def _do_local_scan(self):
        """探测本机开放的 ADB 端口（模拟器）"""
        from ...core.android import scan_and_connect_local

        def progress_cb(message: str, current: int, total: int):
            self.wireless_progress.emit(message, current, total)

        devices = scan_and_connect_local(
            progress_cb=progress_cb,
            cancel_check=self.is_cancelled,
        )
        if not self._cancelled:
            self.wireless_finished.emit(devices)

    def _on_progress(self, message: str, current: int, total: int):
        """进度回调（后台线程），通过信号发送到主线程"""
        self.wireless_progress.emit(message, current, total)

    def _do_connect(self):
        from ...core.android import AdbDevice, connect_agent, create_capture_backend

        device = AdbDevice(serial=self._serial)
        w, h = device.get_resolution()
        if w <= 0 or h <= 0:
            self.error.emit(tr("无法获取设备分辨率"))
            return
        # 在后台线程读取稳定身份，避免把无线 ADB 的 IP:端口当成目标主键，
        # 也避免在主线程额外执行 adb shell。
        device.get_stable_identity()

        # 设备端代理只负责输入手势；截图严格服从用户选择的 screencap / scrcpy。
        # 这样启用 Beta 输入通道不会暗中改变截图命令。
        agent = None
        method = self._capture_method
        if self._agent_mode:
            agent = connect_agent(device)
            if agent is None:
                self.notice.emit(tr("[设备端执行] App 不可达或设备端执行通道未就绪，回退 adb shell input"))
            else:
                self.notice.emit(f"[设备端执行] 已连接 {agent.describe()}")

        capture = create_capture_backend(device=device, method=method)
        started = capture.start()
        if not started:
            if agent is not None:
                agent.close()
            # 后端若给出了具体原因（如 scrcpy 协议版本对不上），原样展示；
            # 否则只能说"不可用"，用户还得自己翻日志。
            reason = str(getattr(capture, "last_error", "") or "")
            self.error.emit(
                f"{method} {reason}" if reason
                else tr("{method} 截图后端不可用").format(method=method))
            return

        self.connect_finished.emit(device, capture, method, w, h, agent)


class _WirelessScanDialog(QObject):
    """未发现 ADB 设备时的扫描对话框

    多网卡（VMware/WSL/VPN 虚拟网卡）环境下，只扫一个网段常常扫不到目标设备，
    因此这里把本机网卡+网段列进下拉框，默认「全部网卡」逐段扫；
    模拟器只在 127.0.0.1 上监听，另给一个「本地扫描」入口。
    """

    def __init__(self, parent):
        super().__init__(parent)
        from PyQt6.QtWidgets import (
            QDialog,
            QVBoxLayout,
        )
        self._dialog = QDialog(parent)
        self._dialog.setWindowTitle(tr("未发现 ADB 设备"))
        self._dialog.setMinimumWidth(460)

        layout = QVBoxLayout(self._dialog)

        self._panel = DeviceScanPanel(self._dialog)
        self._panel.scan_requested.connect(self._begin_scan)
        self._panel.cancel_requested.connect(self._dialog.reject)
        layout.addWidget(self._panel)

        # 回调
        self._on_scan_callback = None
        self._scan_started = False

    def _begin_scan(self, mode: str, subnets: object):
        """进入扫描中状态并回调上层"""
        self._scan_started = True
        if self._on_scan_callback:
            selected = subnets if isinstance(subnets, list) else None
            self._on_scan_callback(mode, selected)

    def update_progress(self, message: str, current: int, total: int):
        """更新进度"""
        self._panel.update_progress(message, current, total)

    def exec(self, on_scan_callback) -> bool:
        """显示对话框，返回是否发起过扫描

        on_scan_callback(mode, subnets)：mode 为 'lan' / 'local'，
        subnets 为选中的网段前缀列表（None = 全部网卡）。
        """
        from PyQt6.QtWidgets import QDialog
        self._on_scan_callback = on_scan_callback
        return self._dialog.exec() == QDialog.DialogCode.Accepted

    @property
    def scan_started(self) -> bool:
        return self._scan_started

    def accept(self):
        """接受对话框（扫描完成）"""
        self._dialog.accept()

    def reject(self):
        """拒绝对话框（取消）"""
        self._dialog.reject()


class WindowOpsMixin:
    """窗口扫描/定位/截屏混入类

    依赖主类提供:
        _target_window, _scanned_windows, _overlay, _capture, _last_capture,
        _layout_manager, _running, btn_locate, window_combo,
        preview_label, log_text, statusBar(), _refresh_run_button()
    scrcpy 帧信号:
        主类需定义 _scrcpy_frame_ready = pyqtSignal(object) 并连接 _on_scrcpy_frame_ui
    """

    # 流式截图态的类级兜底：连接成功前被读也有明确默认值
    _scrcpy_streaming = False

    # ─── 已连接目标 / 执行目标 ─────────────────────────────

    def _active_execution_target(self):
        return self._execution_targets.active()

    def _set_locate_enabled(self, enabled: bool) -> None:
        """记录「候选侧是否允许定位」，并与运行锁取与后落到按钮上。

        定位按钮原来由扫描流程的六处 setEnabled 各自决定，运行锁再往里插一处
        就会互相覆盖——扫描结束把它打开，任务还在跑；任务结束把它打开，可是
        下拉框里一个候选都没有。所以候选侧的意图单独存一份，真正的可用性由
        这里统一算。
        """
        self._locate_allowed_by_candidate = enabled
        self._apply_locate_lock()

    def _apply_locate_lock(self) -> None:
        """运行中禁止定位/连接：只能扫描，不能改变连接拓扑。

        定位会替换窗口目标并 dispose 旧目标，而旧目标的 capture 正是运行中引擎
        持有的那一个（快照存的是同一个对象引用），stop 掉它等于把正在跑的任务
        的截图通道拆了。ADB 候选也走这个按钮，顺带堵住「点已连接设备把执行目标
        切走」。
        """
        allowed = getattr(self, "_locate_allowed_by_candidate", False)
        busy = False
        if self._candidate_backend == "windows":
            from .execution_targets import WINDOW_TARGET_ID
            busy = self._target_has_active_run(WINDOW_TARGET_ID)
        elif self._candidate_backend == "adb":
            candidate = self.window_combo.currentData()
            if isinstance(candidate, dict):
                existing = self._execution_targets.device(
                    str(candidate.get("serial") or ""))
                busy = bool(
                    existing is not None
                    and self._target_has_active_run(existing.id)
                )
        self.btn_locate.setEnabled(allowed and not busy)
        self.btn_locate.setToolTip(
            tr("该目标正在执行任务，不能重新定位或连接") if busy else "")

    def _sync_active_target_compat(self) -> None:
        """把当前执行目标投影到既有单目标字段。

        大量场景、采集和工作流入口仍通过这些字段访问后端。目标列表在运行中
        同样可以切换（运行实例持有自己的冻结快照），因此这些字段只代表“当前
        查看的目标”，运行线程一律不得读取，否则切换观察目标就会让引擎改道。
        """
        target = self._active_execution_target()
        if target is None:
            self._backend = None
            self._capture = None
            self._input = None
            self._device = None
            self._device_ready = False
            self._target_window = None
            self._agent = None
            self._scrcpy_streaming = False
            self._last_capture = None
            return
        self._backend = target.kind
        self._capture = target.capture
        self._input = target.input_ctrl
        self._device = target.device
        self._device_ready = target.kind == "adb" and target.ready
        self._target_window = target.window
        self._agent = target.agent
        self._scrcpy_streaming = target.streaming
        self._last_capture = target.last_capture
        # 无条件覆盖：窗口目标没有 resume_event，若这里跳过赋值，字段会继续
        # 指向上一台设备，停止窗口任务时就会误唤醒那台设备等待重连的任务。
        self._adb_resume_event = target.resume_event
        from ...core.app_controller import set_active_connected_target
        set_active_connected_target(target.id)

    def _store_active_target_compat(self) -> None:
        """把兼容字段上的后端替换写回当前目标。"""
        target = self._active_execution_target()
        if target is None:
            return
        target.capture = self._capture
        target.input_ctrl = self._input
        target.input_kind = str(getattr(self._input, "kind", "") or "")
        target.window = self._target_window
        target.device = self._device
        target.agent = getattr(self, "_agent", None)
        target.streaming = self._scrcpy_streaming
        target.last_capture = self._last_capture

    @staticmethod
    def _target_size_details(target) -> str:
        if target.kind == "adb":
            # serial 已经是名称列的内容，不在相邻一列再重复一遍
            return f"{target.width}×{target.height}" \
                if target.width and target.height else ""
        window = target.window or {}
        return f"{window.get('width', 0)}×{window.get('height', 0)}"

    @staticmethod
    def _target_connection_details(target) -> str:
        """连接信息列：固定「输入方式 · 截图方式」，每段四个汉字。

        顺序与连接选项的两个复选框槽位一致（槽 1 怎么操作、槽 2 怎么取画面），
        两处可以直接对读。原来 ADB 分支是「截图 · 执行」的反序，而且把
        ``screencap`` / ``scrcpy`` / ``ADB`` 这些实现 key 原样摆给用户看。
        """
        if target.kind == "adb":
            input_mode = tr("端侧执行") if target.agent is not None \
                else tr("指令执行")
            # 截图方式只可能是 scrcpy / screencap（UserConfig 有白名单校验）；
            # 设备端代理只接管输入，不改变截图命令
            capture_mode = tr("流式截图") \
                if target.capture_method == "scrcpy" else tr("单帧截图")
        else:
            from ...core.desktop import WgcCapture
            input_mode = tr("后台输入") \
                if bool(getattr(target.input_ctrl, "background_mode", False)) \
                else tr("前台输入")
            capture_mode = tr("后台截图") \
                if isinstance(target.capture, WgcCapture) else tr("前台截图")
        return tr("{input_mode} · {capture_mode}").format(
            input_mode=input_mode,
            capture_mode=capture_mode,
        )

    def _target_details(self, target) -> str:
        """日志使用的完整摘要；UI 分列展示连接与状态。"""
        return " · ".join(part for part in (
            self._target_size_details(target),
            self._target_connection_details(target),
        ) if part)

    def _refresh_execution_targets_ui(self) -> None:
        tree = self.execution_target_list
        selected_id = self._execution_targets.active_target_id
        tree.blockSignals(True)
        tree.clear()
        selected_item = None
        for target in self._execution_targets.all():
            manager = getattr(self, "_run_manager", None)
            run_context = manager.run_for_target(target.id) \
                if manager is not None else None
            if run_context is None:
                state_text = tr("已连接") if target.ready else tr("已离线")
            else:
                state_text = {
                    "starting": tr("启动中"),
                    "running": tr("运行中"),
                    "pausing": tr("暂停中"),
                    "paused": tr("已暂停"),
                    "waiting_target": tr("等待重连"),
                    "stopping": tr("结束中"),
                }.get(run_context.state.value, tr("运行中"))
            item = QTreeWidgetItem([
                target.display_name,
                state_text,
                self._target_size_details(target),
                self._target_connection_details(target),
                "×",
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, target.id)
            if self._identity_unconfirmed(target):
                # 连接信息列的格式由「四个汉字 · 四个汉字」固定，这类少见的
                # 身份问题放 tooltip，不挤占那两段。
                item.setToolTip(0, tr(
                    "设备标识未确认：只能按当前连接地址区分，重启或换用另一种"
                    "连接方式后会被视为新目标"))
            item.setTextAlignment(4, Qt.AlignmentFlag.AlignCenter)
            item.setToolTip(
                4, tr("断开定位") if target.kind == "windows"
                else tr("断开连接"))
            tree.addTopLevelItem(item)
            if target.id == selected_id:
                selected_item = item
        if selected_item is not None:
            tree.setCurrentItem(selected_item)
        self._resize_execution_target_columns()
        tree.blockSignals(False)

    def _resize_execution_target_columns(self) -> None:
        """四个信息列均分剩余空间；文本放不下时滚动，操作列保持独立留白。"""
        if getattr(self, "_target_columns_resizing", False):
            return
        self._target_columns_resizing = True
        try:
            tree = self.execution_target_list
            metrics = tree.fontMetrics()
            content_width = max(
                [metrics.horizontalAdvance(tree.headerItem().text(column))
                 for column in range(4)] +
                [metrics.horizontalAdvance(tree.topLevelItem(row).text(column))
                 for row in range(tree.topLevelItemCount()) for column in range(4)])
            action_width = metrics.horizontalAdvance("×") + 32
            width = max(content_width + 24, (tree.viewport().width() - action_width) // 4)
            for column, desired in enumerate([width] * 4 + [action_width]):
                if tree.columnWidth(column) != desired:
                    tree.setColumnWidth(column, desired)
        finally:
            self._target_columns_resizing = False

    @staticmethod
    def _execution_target_id_from_item(item) -> str:
        if item is None:
            return ""
        return str(item.data(0, Qt.ItemDataRole.UserRole) or "")

    def _on_execution_target_item_clicked(self, item, column: int) -> None:
        if column != 4:
            return
        target_id = self._execution_target_id_from_item(item)
        if not target_id:
            return
        # 断开会 tree.clear() 掉触发本信号的那个 item。推到下一轮事件循环，
        # 让 Qt 先把这次点击处理完；捕获的是稳定 ID 而不是 item，所以推迟
        # 不会指错目标——注册表按 ID 索引正是为这种场合准备的。
        QTimer.singleShot(
            0, lambda: self._disconnect_execution_target(target_id))

    def _on_execution_target_context_menu(self, pos) -> None:
        item = self.execution_target_list.itemAt(pos)
        target_id = self._execution_target_id_from_item(item)
        target = self._execution_targets.get(target_id)
        if target is None:
            return
        menu = QMenu(self.execution_target_list)
        if target.kind == "windows":
            background_action = menu.addAction(tr("后台输入"))
            assert background_action is not None
            background_action.setCheckable(True)
            background_action.setChecked(
                bool(getattr(target.input_ctrl, "background_mode", False)))
            from ...core.desktop import WgcCapture
            capture_action = menu.addAction(tr("后台截图"))
            assert capture_action is not None
            capture_action.setCheckable(True)
            capture_action.setChecked(isinstance(target.capture, WgcCapture))
            marker_action = menu.addAction(tr("红框标定"))
            assert marker_action is not None
            marker_action.setCheckable(True)
            marker_action.setChecked(self.chk_red_box.isChecked())
            menu.addSeparator()
            refresh_action = menu.addAction(tr("刷新位置"))
            disconnect_action = menu.addAction(tr("断开定位"))
            assert refresh_action is not None
            assert disconnect_action is not None
            chosen = menu.exec(
                self.execution_target_list.viewport().mapToGlobal(pos))
            if chosen is background_action:
                self._set_window_target_background_input(
                    target_id, background_action.isChecked())
            elif chosen is capture_action:
                self._set_window_target_background_capture(
                    target_id, capture_action.isChecked())
            elif chosen is marker_action:
                self._set_window_target_marker(
                    target_id, marker_action.isChecked())
            elif chosen is refresh_action:
                self._refresh_or_reconnect_execution_target(target_id)
            elif chosen is disconnect_action:
                self._disconnect_execution_target(target_id)
        else:
            capture_action = menu.addAction(tr("流式截图"))
            assert capture_action is not None
            capture_action.setCheckable(True)
            capture_action.setChecked(target.capture_method == "scrcpy")
            execution_action = menu.addAction(tr("端侧执行"))
            assert execution_action is not None
            execution_action.setCheckable(True)
            execution_action.setChecked(target.agent is not None)
            menu.addSeparator()
            refresh_action = menu.addAction(tr("重新连接"))
            disconnect_action = menu.addAction(tr("断开连接"))
            assert refresh_action is not None
            assert disconnect_action is not None
            chosen = menu.exec(
                self.execution_target_list.viewport().mapToGlobal(pos))
            if chosen is capture_action:
                self._set_android_target_streaming(
                    target_id, capture_action.isChecked())
            elif chosen is execution_action:
                self._reconnect_android_target(
                    target_id, device_execution=execution_action.isChecked())
            elif chosen is refresh_action:
                self._refresh_or_reconnect_execution_target(target_id)
            elif chosen is disconnect_action:
                self._disconnect_execution_target(target_id)

    def _on_execution_target_selected(self, current, _previous=None) -> None:
        if current is None:
            return
        target_id = current.data(0, Qt.ItemDataRole.UserRole)
        if not target_id or target_id == self._execution_targets.active_target_id:
            return
        self._capture_launch_draft(self._execution_targets.active_target_id)
        # 这里不中断录制：录制来源已在开始时冻结，切换观察目标只改预览。
        # 用 select 的返回值，而不是回头再查一次 active()：后者的返回类型是
        # 可空的，加守卫等于承认这里可能是 None，而实际上不可能。
        target = self._execution_targets.select(str(target_id))
        self._restore_active_target_view()
        self.log_text.append(
            tr("[执行目标] 已切换到 {name}").format(name=target.display_name))

    def _restore_active_target_view(self) -> None:
        """手动选中和删除后的自动选中共用完整投影，不依赖列表选择信号。"""
        target_id = self._execution_targets.active_target_id
        self._project_run_context_for_target(target_id)
        run_context = self._run_manager.run_for_target(target_id) if target_id else None
        frozen_draft = (
            run_context.metadata.get("launch_draft")
            if run_context is not None else None)
        if target_id:
            self._restore_launch_draft(target_id, frozen_draft)
        batch_tab = getattr(self, "_batch_tab", None)
        if batch_tab is not None:
            batch_tab.show_run(
                run_context.task_run_id
                if run_context is not None and run_context.metadata.get("batch")
                else "")
        self._sync_active_target_compat()
        self._refresh_active_target_ui()
        self._refresh_run_button()
        redraw_logs = getattr(self, "_redraw_log_events", None)
        if callable(redraw_logs):
            redraw_logs()

    def _refresh_active_target_ui(self) -> None:
        target = self._active_execution_target()
        if target is None:
            self.preview_label.clear()
            self.preview_label.setText(tr("连接目标后可预览"))
            refresh_capture_state = getattr(self, "_apply_rec_state", None)
            if callable(refresh_capture_state):
                refresh_capture_state()
            return
        if target.last_capture is not None:
            self._show_preview_image(target.last_capture)
        elif target.ready:
            self._capture_preview()
        refresh_capture_state = getattr(self, "_apply_rec_state", None)
        if callable(refresh_capture_state):
            refresh_capture_state()

    def _refresh_connection_draft_ui(self, mode: str | None) -> None:
        """显示候选类型的连接草稿，不读取当前执行目标。"""
        is_adb = mode == "adb"
        is_windows = mode == "windows"
        self.chk_scrcpy.setVisible(is_adb)
        self.chk_agent.setVisible(is_adb)
        self.chk_bg_mode.setVisible(is_windows)
        self.chk_bg_capture.setVisible(is_windows)
        self.chk_red_box.setVisible(False)

        for checkbox in (
                self.chk_scrcpy, self.chk_agent,
                self.chk_bg_mode, self.chk_bg_capture):
            checkbox.setEnabled(not self._running)
        if is_adb:
            self.chk_scrcpy.blockSignals(True)
            self.chk_scrcpy.setChecked(
                self._android_connection_draft.capture_method == "scrcpy")
            self.chk_scrcpy.blockSignals(False)
            self.chk_agent.blockSignals(True)
            self.chk_agent.setChecked(
                self._android_connection_draft.device_execution)
            self.chk_agent.blockSignals(False)
        elif is_windows:
            from ...core.desktop import wgc_available
            available, unavailable_reason = wgc_available()
            if not self._window_connection_draft.background_input:
                self._window_connection_draft.background_capture = False
                reason = tr(
                    "需要先勾选「后台模式」：前台输入要求窗口在前台，配后台截图没有意义")
            elif not available:
                self._window_connection_draft.background_capture = False
                reason = unavailable_reason
            elif self._running:
                reason = tr("运行中不能修改下一次连接参数")
            else:
                reason = ""
            self.chk_bg_mode.blockSignals(True)
            self.chk_bg_mode.setChecked(
                self._window_connection_draft.background_input)
            self.chk_bg_mode.blockSignals(False)
            self.chk_bg_capture.blockSignals(True)
            self.chk_bg_capture.setChecked(
                self._window_connection_draft.background_capture)
            self.chk_bg_capture.blockSignals(False)
            self.chk_bg_capture.setEnabled(not reason)
            self.chk_bg_capture.setToolTip(reason or bg_capture_tip())

    # ─── 后端模式切换 ──────────────────────────────────────

    def _apply_backend_ui(self, mode: str):
        """根据当前候选类型调整连接按钮；不改变执行目标。"""
        self._candidate_backend = mode
        if mode == "adb":
            self.btn_locate.setText(tr("连接"))
        else:
            self.btn_locate.setText(tr("定位"))
        self._refresh_connection_draft_ui(mode)

    # ─── 窗口扫描 ──────────────────────────────────────────

    def _on_scan_window(self):
        """扫描所有可见窗口；只刷新候选，不改变连接或执行目标。"""
        from ...core.platforms import DESKTOP_BACKEND_AVAILABLE
        if not DESKTOP_BACKEND_AVAILABLE:
            # 按钮在非 Windows 已隐藏，此处为防御：投屏模式依赖 Win32 API
            self.log_text.append(tr("[提示] 当前平台不支持窗口投屏模式，请使用「扫描设备」"))
            return
        self._apply_backend_ui("windows")

        self._set_locate_enabled(False)
        self.statusBar().showMessage(tr("正在扫描窗口..."))

        from ...core.desktop import list_visible_windows
        self._scanned_windows = list_visible_windows()
        self.window_combo.clear()

        if not self._scanned_windows:
            self.log_text.append(tr("[错误] 未找到可见窗口"))
            self.statusBar().showMessage(tr("未定位窗口 | 未找到可见窗口"))
            return

        for w in self._scanned_windows:
            self.window_combo.addItem(
                f"{w['title']}  ({w['width']}x{w['height']})",
                w,
            )

        # 自动匹配 window_title（配置管理保存的 desktop_window_title）
        keyword = self._user_config.desktop_window_title
        if keyword:
            match_index = _find_auto_connect_window_index(
                self._scanned_windows, keyword)
            if match_index is not None:
                window = self._scanned_windows[match_index]
                self.window_combo.setCurrentIndex(match_index)
                self._on_locate_window()
                self.log_text.append(
                    f"[扫描] 已自动匹配窗口: {window['title']}（关键字: {keyword}）")
                return
            self.log_text.append(f"[扫描] 找到 {len(self._scanned_windows)} 个窗口，未匹配到关键字「{keyword}」")
        else:
            self.log_text.append(f"[扫描] 找到 {len(self._scanned_windows)} 个窗口，请下拉选择目标窗口")
        self._set_locate_enabled(True)
        self.statusBar().showMessage(tr("已扫描窗口 | 请下拉选择目标窗口并点击定位"))

    def _on_window_selected(self, index):
        """下拉框选择了某项时，启用定位按钮"""
        self._set_locate_enabled(index >= 0)

    # ─── ADB 设备扫描/连接 ─────────────────────────────────

    def _on_scan_devices(self):
        """扫描 ADB 设备；只刷新候选，不断开已连接目标。"""
        if self._device_scan_running():
            self._cancel_device_scan()
            return
        self._apply_backend_ui("adb")

        self._set_locate_enabled(False)
        self.btn_scan_window.setEnabled(False)
        self.btn_scan_device.setEnabled(True)
        self.btn_scan_device.setText(tr("取消扫描"))
        self.statusBar().showMessage(tr("正在扫描设备..."))

        # 异步扫描
        self._wait_device_thread()
        self._device_thread = QThread()
        self._device_worker = _DeviceWorker(task="scan")
        self._device_worker.moveToThread(self._device_thread)
        self._device_thread.started.connect(self._device_worker.run)
        self._device_worker.scan_finished.connect(self._on_scan_devices_done)
        self._device_worker.error.connect(self._on_scan_devices_error)
        self._bind_device_worker_lifecycle(self._device_worker, self._device_thread)
        self._device_thread.start()

    def _on_scan_devices_done(self, devices: list):
        """扫描完成回调（主线程）"""
        self.btn_scan_window.setEnabled(True)
        self.btn_scan_device.setEnabled(True)
        self.btn_scan_device.setText(tr("扫描设备"))
        self._scanned_windows = devices
        self.window_combo.clear()

        if not devices:
            self.log_text.append(tr("[提示] 未发现 USB 连接的 ADB 设备"))
            self.statusBar().showMessage(tr("未发现设备 | 可尝试局域网扫描"))
            # 弹出对话框询问是否扫描局域网
            self._ask_wireless_scan()
            return

        for d in devices:
            label = d["serial"] + (f"  ({d['model']})" if d["model"] else "")
            self.window_combo.addItem(label, d)
        self._set_locate_enabled(True)
        self.log_text.append(f"[扫描] 找到 {len(devices)} 台设备，请选择并点击连接")
        self.statusBar().showMessage(tr("已扫描设备 | 请选择设备并点击连接"))

    def _ask_wireless_scan(self):
        """询问用户是否扫描局域网 ADB 设备"""
        self._wireless_dialog = _WirelessScanDialog(self)
        # 延迟到下一轮事件循环显示模态对话框，确保调用栈已返回事件循环
        QTimer.singleShot(0, self._show_wireless_dialog)

    def _show_wireless_dialog(self):
        """显示局域网扫描对话框"""
        result = self._wireless_dialog.exec(on_scan_callback=self._start_wireless_scan)
        if not result:
            if self._wireless_dialog.scan_started:
                # 线程退出后再恢复按钮，避免紧接着启动新扫描而阻塞 UI。
                self._cancel_wireless_scan()
            else:
                self.btn_scan_device.setEnabled(True)
                self.btn_scan_device.setText(tr("扫描设备"))
                self.statusBar().showMessage(tr("已取消扫描"))

    def _cancel_wireless_scan(self):
        """请求取消无线扫描；实际退出由工作线程协作完成。"""
        self._cancel_device_scan()

    def _device_scan_running(self) -> bool:
        worker = getattr(self, "_device_worker", None)
        thread = getattr(self, "_device_thread", None)
        return bool(
            worker is not None
            and getattr(worker, "_task", "") in {
                "scan", "wireless_scan", "local_scan",
            }
            and thread is not None
            and thread.isRunning()
        )

    def _cancel_device_scan(self):
        """立即反馈取消状态，不在 UI 主线程等待扫描线程。"""
        worker = getattr(self, "_device_worker", None)
        if worker is None or not self._device_scan_running():
            self.btn_scan_device.setEnabled(True)
            self.btn_scan_device.setText(tr("扫描设备"))
            return
        worker.cancel()
        self.btn_scan_device.setEnabled(False)
        self.btn_scan_device.setText(tr("取消中..."))
        self.statusBar().showMessage(tr("正在取消扫描..."))

    def _bind_device_worker_lifecycle(self, worker, thread):
        """保证成功、失败和取消三条路径最终都能退出 QThread。"""
        worker.finished.connect(thread.quit)
        thread.finished.connect(
            lambda worker=worker: self._on_device_worker_finished(worker))

    def _on_device_worker_finished(self, worker):
        if worker is not getattr(self, "_device_worker", None):
            return
        if worker.is_cancelled():
            self.btn_scan_window.setEnabled(True)
            self.btn_scan_device.setEnabled(True)
            self.btn_scan_device.setText(tr("扫描设备"))
            self.statusBar().showMessage(tr("已取消扫描"))

    def _wait_device_thread(self):
        """等待可能存在的旧设备线程退出"""
        if hasattr(self, '_device_thread') and self._device_thread.isRunning():
            self._device_thread.quit()
            self._device_thread.wait(3000)

    def _start_wireless_scan(self, mode: str = "lan", subnets: list | None = None):
        """启动 ADB 扫描（异步）

        Args:
            mode: 'lan' 扫描局域网网段，'local' 探测本机模拟器端口
            subnets: 指定网段前缀列表，None 表示全部网卡（仅 lan 模式有效）
        """
        self.btn_scan_device.setEnabled(False)
        self.btn_scan_window.setEnabled(False)
        if mode == "local":
            self.btn_scan_device.setText(tr("扫描本机..."))
            self.statusBar().showMessage(tr("正在扫描本机模拟器..."))
            self.log_text.append(tr("[扫描] 正在探测本机模拟器 ADB 端口..."))
        else:
            self.btn_scan_device.setText(tr("扫描局域网..."))
            self.statusBar().showMessage(tr("正在扫描局域网..."))
            scope = "全部网卡" if not subnets else "、".join(f"{s}0/24" for s in subnets)
            self.log_text.append(f"[扫描] 正在扫描局域网 ADB 设备（{scope}）...")

        self._wait_device_thread()
        self._device_thread = QThread()
        self._device_worker = _DeviceWorker(
            task="local_scan" if mode == "local" else "wireless_scan",
            subnets=subnets,
        )
        self._device_worker.moveToThread(self._device_thread)
        self._device_thread.started.connect(self._device_worker.run)
        self._device_worker.wireless_finished.connect(self._on_wireless_scan_done)
        self._device_worker.wireless_progress.connect(self._on_wireless_scan_progress)
        self._device_worker.error.connect(self._on_wireless_scan_error)
        self._bind_device_worker_lifecycle(self._device_worker, self._device_thread)
        self._device_thread.start()

    def _on_wireless_scan_progress(self, message: str, current: int, total: int):
        """局域网扫描进度回调（主线程）"""
        # 对话框可能已关闭，检查有效性
        if not hasattr(self, "_wireless_dialog") or not self._wireless_dialog:
            return
        try:
            self._wireless_dialog.update_progress(message, current, total)
        except RuntimeError:
            pass  # 对话框已被销毁

    def _on_wireless_scan_done(self, devices: list):
        """局域网扫描完成回调（主线程）"""
        # 如果任务被取消，不处理结果
        if (hasattr(self, '_device_worker') and self._device_worker
                and self._device_worker.is_cancelled()):
            return

        from PyQt6.QtWidgets import QMessageBox
        self.btn_scan_window.setEnabled(True)
        self.btn_scan_device.setEnabled(True)
        self.btn_scan_device.setText(tr("扫描设备"))
        self.window_combo.clear()

        # 关闭对话框（可能已关闭）
        if hasattr(self, "_wireless_dialog") and self._wireless_dialog:
            try:
                self._wireless_dialog.accept()
            except RuntimeError:
                pass  # 对话框已被销毁

        if not devices:
            local_mode = getattr(self._device_worker, "_task", "") == "local_scan"
            if local_mode:
                self.log_text.append(tr("[扫描] 本机未发现开放的模拟器 ADB 端口"))
                self.statusBar().showMessage(tr("未发现设备 | 请确认模拟器已启动"))
                QMessageBox.warning(
                    self,  # type: ignore[arg-type]  # mixin: self is QWidget
                    tr("未发现设备"),
                    tr("本机未发现开放的模拟器 ADB 端口。\n\n"
                       "请确认：\n"
                       "1. 模拟器已启动\n"
                       "2. 模拟器已开启 ADB 调试"),
                )
            else:
                self.log_text.append(tr("[扫描] 局域网内未发现可连接的 ADB 设备"))
                self.statusBar().showMessage(tr("未发现设备 | 请确认设备已开启无线调试"))
                QMessageBox.warning(
                    self,  # type: ignore[arg-type]  # mixin: self is QWidget
                    tr("未发现设备"),
                    tr("局域网内未发现可连接的 ADB 设备。\n\n"
                       "请确认：\n"
                       "1. 设备与电脑在同一局域网\n"
                       "2. 设备已开启无线调试（开发者选项）\n"
                       "3. 多网卡时可在下拉框改选目标网段，或改用本地扫描"),
                )
            return

        self._scanned_windows = devices
        for d in devices:
            label = d["serial"] + (f"  ({d['model']})" if d.get("model") else "")
            self.window_combo.addItem(label, d)
        self._set_locate_enabled(True)
        self.log_text.append(f"[扫描] 发现 {len(devices)} 台设备，请选择并点击连接")
        self.statusBar().showMessage(f"已发现 {len(devices)} 台设备 | 请选择并点击连接")

    def _on_wireless_scan_error(self, error_msg: str):
        """局域网扫描失败回调（主线程）"""
        self.btn_scan_window.setEnabled(True)
        self.btn_scan_device.setEnabled(True)
        self.btn_scan_device.setText(tr("扫描设备"))
        # 关闭对话框（可能已关闭）
        if hasattr(self, "_wireless_dialog") and self._wireless_dialog:
            try:
                self._wireless_dialog.reject()
            except RuntimeError:
                pass  # 对话框已被销毁
        logger.error(f"局域网扫描失败: {error_msg}")
        self.log_text.append(f"[错误] 局域网扫描失败: {error_msg}")
        self.statusBar().showMessage(tr("扫描失败 | 详见日志"))

    def _on_scan_devices_error(self, error_msg: str):
        """扫描失败回调（主线程）"""
        self.btn_scan_window.setEnabled(True)
        self.btn_scan_device.setEnabled(True)
        self.btn_scan_device.setText(tr("扫描设备"))
        logger.error(f"扫描设备失败: {error_msg}")
        self.log_text.append(f"[错误] 扫描设备失败: {error_msg}")
        self.statusBar().showMessage(tr("扫描失败 | 详见日志"))

    def _on_connect_device(self):
        """异步连接候选设备；既有窗口和其他设备保持连接。"""
        d = self.window_combo.currentData()
        if not d:
            return
        existing = self._execution_targets.device(d["serial"])
        if existing is not None and existing.ready:
            self._execution_targets.select(existing.id)
            self._sync_active_target_compat()
            self._refresh_execution_targets_ui()
            self._refresh_active_target_ui()
            self._refresh_run_button()
            self.statusBar().showMessage(
                tr("设备已经连接，已切换为执行目标"))
            return

        self._start_device_connection(d)

    def _start_device_connection(
            self, combo_data: dict, *, capture_method: str | None = None,
            device_execution: bool | None = None,
            update_candidate_ui: bool = True) -> None:
        """启动指定设备连接；成功前保留同 serial 的既有目标。"""
        if update_candidate_ui:
            self._apply_backend_ui("adb")

        # UI 进入连接中状态
        if update_candidate_ui:
            self._set_locate_enabled(False)
            self.btn_locate.setText(tr("连接中..."))
        self.statusBar().showMessage(tr("正在连接设备..."))

        capture_method = (
            capture_method or self._android_connection_draft.capture_method)
        if device_execution is None:
            device_execution = self._android_connection_draft.device_execution

        # 异步连接
        self._wait_device_thread()
        self._device_thread = QThread()
        self._device_worker = _DeviceWorker(
            task="connect", serial=combo_data["serial"],
            capture_method=capture_method,
            agent_mode=device_execution,
        )
        self._device_worker.moveToThread(self._device_thread)
        self._device_thread.started.connect(self._device_worker.run)
        self._device_worker.notice.connect(self.log_text.append)
        self._device_worker.connect_finished.connect(
            lambda device, capture, method, w, h, agent: self._on_connect_done(
                combo_data, device, capture, method, w, h, agent,
                update_candidate_ui=update_candidate_ui)
        )
        self._device_worker.error.connect(
            lambda message: self._on_connect_error(
                message, update_candidate_ui=update_candidate_ui))
        self._bind_device_worker_lifecycle(self._device_worker, self._device_thread)
        self._device_thread.start()

    def _on_connect_done(
            self, combo_data, device, capture, capture_method, w, h,
            agent=None, *, update_candidate_ui: bool = True):
        """连接成功回调（主线程）"""
        import threading

        from ...core.android import create_input_backend
        from .execution_targets import ExecutionTarget, android_target_id

        # 创建输入控制器：有设备端代理走无障碍手势，否则 adb shell input
        input_ctrl = create_input_backend(
            device=device, input_sim=self._user_config.input_sim, agent=agent)
        identity = device.get_stable_identity()
        target_id = android_target_id(identity.value)
        conflict = self._unconfirmed_identity_conflict(target_id, identity.stable)
        if conflict is not None:
            # 读不到任何稳定标识时，无法判断这条 transport 是不是已连目标的
            # 另一条通道。放行就可能让同一台设备变成两个可执行目标，两个任务
            # 各自以为独占它；自动归并又可能把两台设备错当成一台。两者都不能
            # 猜，所以拦在前台应用探测、历史登记这些副作用之前。
            self._release_rejected_connection(capture, agent)
            self.log_text.append(tr(
                "[拒绝] 无法读取 {serial} 的稳定设备标识，它可能与已连接的"
                "「{name}」是同一台设备。请先断开该目标再连接。"
            ).format(serial=combo_data["serial"], name=conflict.display_name))
            self.statusBar().showMessage(tr("设备标识未确认，已拒绝连接"))
            if update_candidate_ui:
                self.btn_locate.setText(tr("连接"))
                self._set_locate_enabled(True)
            return
        existing_target = self._execution_targets.get(target_id)
        generation = (
            existing_target.handle.binding().generation + 1
            if existing_target is not None and existing_target.handle is not None
            else 1)

        # scrcpy 模式下订阅帧回调，实现预览区实时视频流
        streaming = False
        if capture_method == "scrcpy":
            from ...core.android import AndroidStreamCapture
            if isinstance(capture, AndroidStreamCapture):
                capture.set_on_frame(
                    lambda frame, tid=target_id, gen=generation:
                    self._on_scrcpy_frame(tid, frame, gen))
                streaming = True
                logger.info("[连接] scrcpy 视频流预览已启用")

        # ── ADB 断连暂停恢复接线 ──
        resume_event = (
            existing_target.resume_event
            if existing_target is not None and existing_target.resume_event is not None
            else threading.Event())
        # 先把旧 AdbDevice 的 transport 更新到新连接，再唤醒正在
        # _handle_connection_error 中等待的失败命令，这样重试不会继续打旧 IP。
        if existing_target is not None and existing_target.device is not None:
            existing_target.device.serial = device.serial
            existing_target.device.adb_path = device.adb_path
        device.resume_event = resume_event
        try:
            from ...core.app_controller import record_connected_android
            app_info = record_connected_android(
                device, width=w, height=h, target_id=target_id)
            if app_info.get("package"):
                self.log_text.append(
                    f"[应用识别] {app_info['package']}/{app_info['activity']}")
        except Exception as exc:  # noqa: BLE001 - 连接不应因前台应用探测失败而失败
            logger.warning(f"ADB 当前应用信息获取失败: {exc}")

        bridge = _AdbConnSignalBridge(target_id)
        bridge.adb_lost.connect(self._on_adb_connection_lost)
        device.on_connection_lost = bridge.notify_lost
        device.stop_check = lambda tid=target_id: bool(
            (run := self._run_manager.run_for_target(tid))
            and run.stop_event.is_set())

        # 名字就是 serial：它唯一、用户能对上（哪根线、哪个 IP），同型号两台设备
        # 也由它分开。不用 target_id 的哈希前缀——那是主键的一截，对用户零信息量；
        # 也不再拼型号——serial 已经把设备认出来了，型号只是重复一遍。
        target = ExecutionTarget(
            id=target_id,
            kind="adb",
            display_name=str(combo_data["serial"]),
            capture=capture,
            input_ctrl=input_ctrl,
            input_kind=str(getattr(input_ctrl, "kind", "") or ""),
            device=device,
            agent=agent,
            serial=combo_data["serial"],
            width=w,
            height=h,
            capture_method=capture_method,
            streaming=streaming,
            resume_event=resume_event,
            connection_bridge=bridge,
            metadata={
                "device_identity_source": identity.source,
                "device_identity_stable": identity.stable,
            },
        )
        old = self._execution_targets.put(target)
        if old is not None:
            self._dispose_execution_target(old)
        # 注册表和 TargetHandle 已切到新资源后才唤醒失败命令。
        resume_event.set()
        run_context = self._run_manager.run_for_target(target_id)
        if run_context is not None and run_context.state.value == "waiting_target":
            from .execution_runs import RunState
            next_state = (
                RunState.PAUSED
                if run_context.pause_event is not None
                and not run_context.pause_event.is_set()
                else RunState.RUNNING)
            self._run_manager.set_state(run_context.task_run_id, next_state)
            self._emit_run_instance_state(run_context, next_state.value)
            banner = getattr(self, "_adb_banner", None)
            if banner is not None and target_id == self._execution_targets.active_target_id:
                banner.setVisible(False)
        self._sync_active_target_compat()

        method_label = tr("流式截图") if capture_method == "scrcpy" \
            else tr("单帧截图")
        method_label = (
            (tr("端侧执行") if agent is not None else tr("指令执行"))
            + "  |  " + method_label)
        self.log_text.append(f"[连接成功] {combo_data['serial']} ({w}x{h}) [{method_label}]")
        if not identity.stable:
            logger.warning(
                f"[连接] {combo_data['serial']} 读不到稳定设备标识，"
                f"目标身份退化为连接地址")
            self.log_text.append(tr(
                "[警告] 该设备未提供稳定标识，USB 与无线连接会被当成两个目标"))
        hk = self._user_config.hotkeys
        self.statusBar().showMessage(self._hotkey_status(
            f"已连接设备 {combo_data['serial']}",
            (hk.start, tr("开始")), (hk.stop, tr("停止"))))
        if update_candidate_ui:
            self.btn_locate.setText(tr("连接"))
            self._set_locate_enabled(True)
        self._refresh_execution_targets_ui()
        self._refresh_active_target_ui()
        self._refresh_run_button()
        # screencap 模式手动刷新预览；scrcpy 模式自动推帧
        if self._execution_targets.active_target_id == target_id and not streaming:
            self._capture_preview()

    def _on_connect_error(
            self, error_msg: str, *, update_candidate_ui: bool = True):
        """连接失败回调（主线程）"""
        logger.error(f"连接设备失败: {error_msg}")
        self.log_text.append(f"[错误] 连接设备失败: {error_msg}")
        self.statusBar().showMessage(tr("连接失败 | 详见日志"))
        if update_candidate_ui:
            self.btn_locate.setText(tr("连接"))
            self._set_locate_enabled(True)

    def _dispose_execution_target(self, target) -> None:
        """释放一个目标独占的资源，不影响其他已连接目标。"""
        capture = target.capture
        if capture is not None:
            try:
                capture.stop()
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"截图后端停止失败: {exc}")
        if target.agent is not None:
            try:
                target.agent.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[设备端执行] 关闭代理失败: {exc}")
        if target.connection_bridge is not None:
            try:
                target.connection_bridge.deleteLater()
            except RuntimeError:
                pass

    def _refresh_bg_mode_lock(self):
        """任务运行状态变化后刷新连接草稿的可编辑状态。"""
        self._refresh_connection_draft_ui(self._candidate_backend)

    def _apply_bg_capture_default(self):
        """后台模式启用时，把配置默认值写入连接草稿。"""
        if self._user_config.desktop_background_capture:
            self._window_connection_draft.background_capture = True
        self._refresh_connection_draft_ui("windows")

    def _disconnect_execution_target(self, target_id: str) -> None:
        """按稳定 ID 断开一个目标，不影响其他连接。"""
        target = self._execution_targets.get(target_id)
        if target is None:
            return
        if self._target_has_active_run(target.id):
            self.log_text.append(tr("[提示] 该目标正在执行任务，不能断开"))
            return
        was_active = target.id == self._execution_targets.active_target_id
        self._abort_recording_for_target(target.id, tr("断开执行目标"))
        removed = self._execution_targets.remove(target.id)
        if removed is not None:
            self._dispose_execution_target(removed)
        if target.kind == "windows":
            self._red_box_flash_timer.stop()
            self._overlay.hide_border()
        from ...core.app_controller import remove_connected_target
        remove_connected_target(target.id)
        self._refresh_execution_targets_ui()
        if was_active:
            # remove 已更新 active_target_id；列表刷新屏蔽信号，不能等点击恢复。
            self._restore_active_target_view()
        else:
            # 删除未选中目标不重载当前目标的草稿和进度。
            self._sync_active_target_compat()
            self._refresh_active_target_ui()
            self._refresh_run_button()
        self.statusBar().showMessage(
            tr("已断开目标：{name}").format(name=target.display_name))
        self.log_text.append(f"[断连] {target.display_name}")

    def _refresh_or_reconnect_execution_target(self, target_id: str) -> None:
        """刷新窗口坐标，或重新建立指定 Android 目标。"""
        target = self._execution_targets.get(target_id)
        if target is None:
            return
        if target.kind == "adb":
            self._reconnect_android_target(target_id)
            return

        window = target.window
        if window is None:
            return
        old_rect = tuple(window.get(key) for key in (
            "left", "top", "width", "height"))
        if not self._refresh_window_rect(window):
            target.status = "offline"
            self._red_box_flash_timer.stop()
            self._overlay.hide_border()
            from ...core.app_controller import remove_connected_target
            remove_connected_target(target.id)
            self._refresh_execution_targets_ui()
            if self._execution_targets.active_target_id == target.id:
                self._sync_active_target_compat()
                self._refresh_run_button()
            message = tr("窗口已消失或句柄失效，请重新定位")
            self.statusBar().showMessage(message)
            self.log_text.append(
                tr("[定位失效] {name}：{message}").format(
                    name=target.display_name, message=message))
            return
        target.status = "connected"
        target.width = int(window.get("width") or 0)
        target.height = int(window.get("height") or 0)
        if target.capture is not None:
            target.capture.set_capture_region(
                int(window.get("left") or 0),
                int(window.get("top") or 0),
                target.width,
                target.height,
            )
        marker_ok = True
        if self.chk_red_box.isChecked():
            marker_ok = self._overlay.show_border(
                int(window.get("left") or 0),
                int(window.get("top") or 0),
                target.width,
                target.height,
            )
        if target.handle is not None:
            target.handle.update_window(window)
        from ...core.app_controller import record_connected_window
        record_connected_window(window)
        if self._execution_targets.active_target_id == target.id:
            self._sync_active_target_compat()
            self._capture_preview()
        run_context = self._run_manager.run_for_target(target.id)
        if run_context is not None and run_context.engine is not None:
            rebind = getattr(run_context.engine, "rebind_target_window", None)
            if callable(rebind):
                rebind(window)
        self._refresh_execution_targets_ui()
        new_rect = tuple(window.get(key) for key in (
            "left", "top", "width", "height"))
        self.statusBar().showMessage(
            tr("已刷新窗口位置") if marker_ok
            else tr("窗口位置已刷新，但红框移动失败"))
        self.log_text.append(
            tr("[定位刷新] {name} · {old} → {new} · {details}").format(
                name=target.display_name, old=old_rect, new=new_rect,
                details=self._target_details(target)))

    def _set_window_target_background_input(
            self, target_id: str, enabled: bool) -> None:
        """只修改指定窗口目标的实际输入后端。"""
        target = self._execution_targets.get(target_id)
        if target is None or target.kind != "windows" or target.window is None:
            return
        if self._target_has_active_run(target.id):
            self.log_text.append(tr("[提示] 该目标正在执行任务，不能切换输入方式"))
            return
        if not enabled:
            from ...core.desktop import WgcCapture
            if isinstance(target.capture, WgcCapture):
                self._set_window_target_background_capture(target_id, False)
        if enabled:
            from ...core.desktop import PostMessageInput
            target.input_ctrl = PostMessageInput(
                input_sim=self._user_config.input_sim,
                hwnd=target.window["hwnd"],
            )
        else:
            from ...core.desktop import SendInputInput
            target.input_ctrl = SendInputInput(
                input_sim=self._user_config.input_sim,
                target_hwnd=target.window["hwnd"],
            )
        target.input_kind = str(
            getattr(target.input_ctrl, "kind", "") or "")
        self._rebind_target_resources(target)
        if self._execution_targets.active_target_id == target_id:
            self._sync_active_target_compat()
        self._refresh_execution_targets_ui()
        self.log_text.append(
            tr("[模式] {name} 已切换到{mode}").format(
                name=target.display_name,
                mode=tr("后台输入") if enabled else tr("前台输入")))

    def _set_window_target_marker(
            self, target_id: str, enabled: bool) -> None:
        """控制唯一窗口目标的持续红框，不改变执行目标。"""
        target = self._execution_targets.get(target_id)
        if target is None or target.kind != "windows" or target.window is None:
            return
        self.chk_red_box.blockSignals(True)
        self.chk_red_box.setChecked(enabled)
        self.chk_red_box.blockSignals(False)
        self._red_box_flash_timer.stop()
        if enabled:
            window = target.window
            self._overlay.show_border(
                window["left"], window["top"],
                window["width"], window["height"])
            self._overlay.set_color("red")
        else:
            self._overlay.hide_border()

    def _set_window_target_background_capture(
            self, target_id: str, enabled: bool) -> None:
        """只修改指定窗口目标的实际截图后端。"""
        target = self._execution_targets.get(target_id)
        if target is None or target.kind != "windows" or target.window is None:
            return
        if self._target_has_active_run(target.id):
            self.log_text.append(tr("[提示] 该目标正在执行任务，不能切换截图方式"))
            return
        if enabled and not bool(
                getattr(target.input_ctrl, "background_mode", False)):
            self._set_window_target_background_input(target_id, True)
        from ...core.desktop import DesktopCapture, WgcCapture, wgc_available
        if enabled:
            available, reason = wgc_available()
            if not available:
                self.log_text.append(tr("[截图] 后台截图不可用：") + reason)
                return
        capture = WgcCapture() if enabled else DesktopCapture()
        window = target.window
        capture.set_capture_region(
            window["left"], window["top"], window["width"], window["height"])
        if isinstance(capture, WgcCapture) \
                and not capture.attach_hwnd(window["hwnd"]):
            capture.stop()
            self.log_text.append(tr("[截图] 后台截图启动失败，保留原截图方式"))
            return
        old_capture = target.capture
        target.capture = capture
        target.capture_method = "wgc" if enabled else "mss"
        target.last_capture = None
        self._rebind_target_resources(target)
        if old_capture is not None:
            try:
                old_capture.stop()
            except Exception as exc:
                logger.debug(f"停止旧截图后端时报错（忽略）: {exc}")
        if self._execution_targets.active_target_id == target_id:
            self._sync_active_target_compat()
            self._capture_preview()
        self._refresh_execution_targets_ui()

    def _set_android_target_streaming(
            self, target_id: str, enabled: bool) -> None:
        """只修改指定设备目标的实际截图后端。"""
        target = self._execution_targets.get(target_id)
        if target is None or target.kind != "adb" or target.device is None:
            return
        if self._target_has_active_run(target.id):
            self.log_text.append(tr("[提示] 该目标正在执行任务，不能切换截图方式"))
            return
        method = "scrcpy" if enabled else "screencap"
        from ...core.android import AndroidStreamCapture, create_capture_backend
        capture = create_capture_backend(device=target.device, method=method)
        if not capture.start():
            self.log_text.append(
                tr("[错误] {method} 截图后端不可用，保留原截图方式").format(
                    method=method))
            return
        self._abort_recording_for_target(target_id, tr("切换截图方式"))
        # 帧回调必须带代次：解码线程在 start() 之后就开始推帧，而
        # _on_scrcpy_frame 的守卫只对携带代次的回调生效。这里预测换绑后的
        # 代次（rebind 固定 +1），否则这条流的旧帧将永远绕过代次校验。
        streaming = False
        if method == "scrcpy" and isinstance(capture, AndroidStreamCapture):
            generation = (target.handle.binding().generation + 1
                          if target.handle is not None else 1)
            capture.set_on_frame(
                lambda frame, tid=target_id, gen=generation:
                self._on_scrcpy_frame(tid, frame, gen))
            streaming = True
        old_capture = target.capture
        target.capture = capture
        target.capture_method = method
        target.streaming = streaming
        target.last_capture = None
        self._rebind_target_resources(target)
        if old_capture is not None:
            try:
                old_capture.stop()
            except Exception as exc:
                logger.debug(f"停止旧截图后端时报错（忽略）: {exc}")
        if self._execution_targets.active_target_id == target_id:
            self._sync_active_target_compat()
            if not streaming:
                self._capture_preview()
            refresh_capture_state = getattr(self, "_apply_rec_state", None)
            if callable(refresh_capture_state):
                refresh_capture_state()
        self._refresh_execution_targets_ui()

    def _reconnect_android_target(
            self, target_id: str, *, device_execution: bool | None = None) -> None:
        """沿用指定设备的实际参数重新连接，可覆盖执行方式。"""
        target = self._execution_targets.get(target_id)
        if target is None or target.kind != "adb":
            return
        if device_execution is None:
            device_execution = target.agent is not None
        self._start_device_connection(
            {"serial": target.serial},
            capture_method=target.capture_method or "screencap",
            device_execution=device_execution,
            update_candidate_ui=False,
        )

    # ─── 窗口定位 ──────────────────────────────────────────

    def _on_locate_window(self):
        """连接当前候选；窗口单例替换，Android 按 serial 累加。"""
        if self._candidate_backend == "adb":
            self._on_connect_device()
            return
        from .execution_targets import WINDOW_TARGET_ID
        if self._target_has_active_run(WINDOW_TARGET_ID):
            self.log_text.append(
                tr("[提示] 窗口目标正在执行任务，不能重新定位"))
            return
        w = self.window_combo.currentData()
        if not w:
            return
        if not self._refresh_window_rect(w):
            message = tr("所选窗口已消失，请重新扫描窗口")
            self.statusBar().showMessage(message)
            self.log_text.append(tr("[定位失败] {message}").format(
                message=message))
            return
        from ...core.app_controller import record_connected_window
        from .execution_targets import WINDOW_TARGET_ID
        record_connected_window(w)

        ratio = self._get_window_dpi_ratio(w["hwnd"])
        logger.info(
            f"目标窗口 Win32原始: ({w['left']},{w['top']},{w['width']}x{w['height']})"
            f" DPI={ratio}"
        )

        self.log_text.append(
            f"[定位成功] {w['title']}  "
            f"({w['width']}x{w['height']} @ {w['left']},{w['top']})"
            + (f" DPI={ratio:.1f}x" if ratio != 1.0 else "")
        )
        self._red_box_flash_timer.stop()
        self._overlay.show_border(w['left'], w['top'], w['width'], w['height'])
        self._overlay.set_color("red")
        if not self.chk_red_box.isChecked():
            self._red_box_flash_timer.start(1000)
        target = self._build_window_execution_target(w)
        old = self._execution_targets.put(target)
        if old is not None:
            self._dispose_execution_target(old)
        self._sync_active_target_compat()
        self._refresh_execution_targets_ui()
        self._refresh_active_target_ui()
        self._refresh_run_button()
        if self._execution_targets.active_target_id == WINDOW_TARGET_ID:
            self._capture_preview()

    def _build_window_execution_target(self, w: dict):
        """为已定位窗口建立独占截图/输入资源。"""
        # 窗口目标有自己的截图和输入实例；连接它不触碰当前 Android 目标。
        from ...core.desktop import (
            DesktopCapture,
            PostMessageInput,
            SendInputInput,
            WgcCapture,
        )
        from .execution_targets import (
            WINDOW_TARGET_ID,
            ExecutionTarget,
            window_target_label,
        )
        want_bg_capture = (
            self._window_connection_draft.background_input
            and self._window_connection_draft.background_capture)
        capture = WgcCapture() if want_bg_capture else DesktopCapture()
        capture.set_capture_region(w["left"], w["top"], w["width"], w["height"])
        if (want_bg_capture and isinstance(capture, WgcCapture)
                and not capture.attach_hwnd(w["hwnd"])):
            capture.stop()
            capture = DesktopCapture()
            capture.set_capture_region(
                w["left"], w["top"], w["width"], w["height"])
            self.chk_bg_capture.blockSignals(True)
            self.chk_bg_capture.setChecked(False)
            self.chk_bg_capture.blockSignals(False)
        actual_bg_capture = isinstance(capture, WgcCapture)
        input_ctrl: object
        if self._window_connection_draft.background_input:
            input_ctrl = PostMessageInput(
                input_sim=self._user_config.input_sim, hwnd=w["hwnd"])
        else:
            input_ctrl = SendInputInput(
                input_sim=self._user_config.input_sim,
                target_hwnd=w["hwnd"],
            )
        return ExecutionTarget(
            id=WINDOW_TARGET_ID,
            kind="windows",
            display_name=window_target_label(w),
            capture=capture,
            input_ctrl=input_ctrl,
            input_kind=str(getattr(input_ctrl, "kind", "") or ""),
            window=w,
            width=int(w.get("width") or 0),
            height=int(w.get("height") or 0),
            capture_method="wgc" if actual_bg_capture else "mss",
        )

    def _hide_red_box_after_locate(self):
        """未勾选标定时，定位成功的红框提示只显示一秒。

        这个定时器只由 _on_locate_window 启动，要收的就是它自己一秒前亮的那个
        框，所以除了「用户有没有要求持续标定」之外不该再有别的条件。

        原来还判 `_backend == "windows"`——那是单目标时代的判据。连接与执行拆成
        正交之后它变成了误判来源，而且有两条触发路径：手机已经是执行目标时去
        定位窗口（put 刻意不夺取选中，_backend 全程是 adb），或者定位后一秒内
        把执行目标切到手机（切换不碰定时器，一秒后读到的已经变了）。两种情况
        都让红框永久留在桌面上。
        """
        if not self.chk_red_box.isChecked():
            self._overlay.hide_border()

    def _on_red_box_changed(self, state):
        """红框标定仅控制本次运行中的持续显示，不写入用户配置。

        红框属于**窗口目标**，与当前执行目标无关，所以直接委托给
        _set_window_target_marker，不再读执行目标投影出来的 _target_window
        ——那个在执行目标是手机时为 None，会让勾选静默无效。

        复选框当前全程 setVisible(False)（唯一入口是目标列表的右键菜单），
        这里保持与右键菜单同源，是为了将来放开它时不必再想一遍。
        """
        from .execution_targets import WINDOW_TARGET_ID
        self._set_window_target_marker(WINDOW_TARGET_ID, bool(state))

    def _on_bg_mode_changed(self, state):
        """修改下一次窗口定位使用的输入方式。"""
        enabled = bool(state)
        self._window_connection_draft.background_input = enabled
        if not enabled:
            self._window_connection_draft.background_capture = False
        elif self._user_config.desktop_background_capture:
            self._window_connection_draft.background_capture = True
        self._refresh_connection_draft_ui("windows")

    def _on_bg_capture_changed(self, state):
        """修改下一次窗口定位使用的截图方式。"""
        if bool(state):
            from ...core.desktop import wgc_available
            ok, reason = wgc_available()
            if not ok:
                self.log_text.append(tr("[截图] 后台截图不可用：") + reason)
                self.chk_bg_capture.blockSignals(True)
                self.chk_bg_capture.setChecked(False)
                self.chk_bg_capture.blockSignals(False)
                return
        self._window_connection_draft.background_capture = bool(state)

    def _rebuild_desktop_capture(self, want_bg: bool | None = None):
        """按目标实际状态重建桌面截图后端并绑定当前窗口。

        后端实例带着会话状态（WGC 的取帧线程），切换时必须整个换掉而不是改标志位。
        WGC 建会话失败就退回 mss 并复位开关——让用户停在一个截不到图的状态比报错更糟。
        """
        from ...core.desktop import DesktopCapture, WgcCapture
        current = self._capture
        if want_bg is None:
            want_bg = isinstance(current, WgcCapture)
        if current is None or isinstance(current, WgcCapture) != want_bg:
            if current is not None:
                try:
                    current.stop()
                except Exception as exc:
                    logger.debug(f"停止旧截图后端时报错（忽略）: {exc}")
            self._capture = WgcCapture() if want_bg else DesktopCapture()

        w = self._target_window
        if not w:
            return self._capture
        capture = self._capture
        assert capture is not None
        capture.set_capture_region(w["left"], w["top"], w["width"], w["height"])
        if isinstance(capture, WgcCapture) and not capture.attach_hwnd(w["hwnd"]):
            self.log_text.append(
                tr("[截图] 后台截图启动失败，已退回前台截图（窗口最小化时用不了）"))
            try:
                capture.stop()
            except Exception:
                pass
            self._capture = DesktopCapture()
            self._capture.set_capture_region(
                w["left"], w["top"], w["width"], w["height"])
        self._store_active_target_compat()
        return self._capture

    def _on_capture_method_changed(self, state):
        """修改下一次设备连接使用的截图方式。"""
        self._android_connection_draft.capture_method = (
            "scrcpy" if state else "screencap")

    def _on_agent_mode_changed(self, state):
        """修改下一次设备连接使用的执行方式。"""
        self._android_connection_draft.device_execution = bool(state)
        label = tr("端侧执行（需安装律匠 App）") if state \
            else tr("指令执行（adb shell input）")
        self.log_text.append(f"[模式] 安卓输入方式: {label}（下次连接生效）")

    # ─── 截屏 ─────────────────────────────────────────────

    def _capture_preview(self):
        """截取已定位窗口/设备的截图并展示在预览区。"""
        img = self._grab_capture_image()
        if img is None:
            if self.preview_label.isVisible():
                self.preview_label.setText(tr("截屏失败"))
            return
        self._show_preview_image(img)

    def _show_preview_image(self, img):
        """把一帧画面渲染到预览区并记为最近一次截图。

        取帧与渲染分开：截屏按钮已经抓过一帧，再调 _capture_preview 会为了刷新
        预览白抓第二次——两帧之间画面可能已经变了，存下来的和看到的还对不上。
        """
        self._last_capture = img
        target = self._active_execution_target()
        if target is not None:
            target.last_capture = img
        try:
            h, w_img = img.shape[:2]
            rgb = np.ascontiguousarray(img[:, :, ::-1])
            fmt = QImage.Format.Format_RGB888
            qimg = QImage(bytes(rgb.data), w_img, h, w_img * 3, fmt).copy()
            pixmap = QPixmap.fromImage(qimg)
            scaled = pixmap.scaled(
                self.preview_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.preview_label.setPixmap(scaled)
            logger.info(f"截屏预览成功 ({w_img}x{h})")
        except Exception as e:
            logger.error(f"截屏预览失败: {e}")
            self.preview_label.setText(f"截屏失败: {e}")

    def _grab_capture_image(self) -> np.ndarray | None:
        """按 backend 获取一帧截图（numpy BGR），失败返回 None"""
        if self._backend == "adb":
            if not self._device_ready or self._capture is None:
                return None
            return self._capture.capture()
        # windows 投屏窗口
        if not self._target_window:
            return None
        capture = self._rebuild_desktop_capture()
        return capture.capture() if capture is not None else None

    def _get_last_capture(self) -> np.ndarray | None:
        """获取最近一次截屏图片（numpy BGR）"""
        return self._last_capture

    def _refresh_capture(self) -> tuple[np.ndarray | None, str | None]:
        """重新截取当前窗口/设备截图（用于场景编辑器刷新）
        返回 (image, error_message)，成功时 error_message 为 None
        """
        if self._backend == "adb":
            if not self._device_ready or self._capture is None:
                return None, tr("请先在主窗口连接设备")
            img = self._capture.capture()
            if img is not None:
                self._last_capture = img
                return img, None
            return None, tr("截图失败")
        if not self._target_window:
            return None, tr("请先在主窗口定位窗口")
        try:
            capture = self._rebuild_desktop_capture()
            img = capture.capture() if capture is not None else None
            if img is not None:
                self._last_capture = img
                return img, None
            return None, tr("截图失败")
        except Exception as e:
            logger.error(f"刷新截图失败: {e}")
            return None, f"截图失败: {e}"

    # ─── 运行期窗口重绑 ───────────────────────────────────

    def _on_target_window_rebound(self, target_id: str, window: dict):
        """工作流重启客户端后回调：把定位状态挪到新窗口上。

        运行在工作流线程，因此只改该运行实例绑定目标的纯数据，不碰任何控件；
        当前 UI 可能正在查看另一台设备，不能再依赖 `_target_window` 投影。
        """
        execution_target = self._execution_targets.get(target_id)
        if execution_target is None or execution_target.window is None:
            return
        target = execution_target.window
        for key in ("hwnd", "pid", "title", "executable",
                    "left", "top", "width", "height"):
            if window.get(key) is not None:
                target[key] = window[key]
        from .execution_targets import window_target_label
        execution_target.display_name = window_target_label(target)
        execution_target.status = "connected"
        if execution_target.handle is not None:
            execution_target.handle.update_window(target)
        logger.info(
            f"[定位跟随] {target_id} 客户端已重启，"
            f"跟随到新窗口 hwnd={target.get('hwnd')}")

    # ─── Win32 工具 ───────────────────────────────────────

    def _refresh_window_rect(self, w: dict) -> bool:
        """验证窗口身份并刷新位置；窗口消失或已换进程时返回 False。"""
        user32 = ctypes.windll.user32
        hwnd_value = int(w.get("hwnd") or 0)
        hwnd = wintypes.HWND(hwnd_value)
        if not hwnd or not user32.IsWindow(hwnd):
            logger.warning(f"刷新窗口位置失败：HWND 已失效 hwnd=0x{hwnd_value:X}")
            return False
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        expected_pid = int(w.get("pid") or 0)
        if not pid.value:
            logger.warning(
                f"刷新窗口位置失败：无法读取窗口进程 hwnd=0x{hwnd_value:X}")
            return False
        if expected_pid and int(pid.value) != expected_pid:
            logger.warning(
                f"刷新窗口位置失败：HWND 已被其他进程复用 "
                f"hwnd=0x{hwnd_value:X} expected_pid={expected_pid} "
                f"actual_pid={pid.value}")
            return False
        rect = wintypes.RECT()
        set_last_error = getattr(ctypes, "set_last_error", None)
        if set_last_error is not None:
            set_last_error(0)
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            get_last_error = getattr(ctypes, "get_last_error", lambda: 0)
            logger.warning(
                f"刷新窗口位置失败：GetWindowRect error={get_last_error()} "
                f"hwnd=0x{hwnd_value:X} pid={pid.value}")
            return False
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 0 or height <= 0:
            logger.warning(
                f"刷新窗口位置失败：窗口矩形无效 hwnd=0x{hwnd_value:X} "
                f"pid={pid.value} rect=({rect.left},{rect.top},"
                f"{rect.right},{rect.bottom})")
            return False
        w['left'] = rect.left
        w['top'] = rect.top
        w['width'] = width
        w['height'] = height
        return True

    def _get_window_dpi_ratio(self, hwnd: int) -> float:
        """返回目标窗口所在屏幕的 DPI 缩放比，仅用于日志展示。"""
        try:
            dpi = ctypes.windll.user32.GetDpiForWindow(wintypes.HWND(hwnd))
            if dpi:
                return dpi / 96
        except Exception as e:
            logger.debug(f"获取窗口 DPI 失败: {e}")
        return 1.0

    # ─── scrcpy 帧回调 ────────────────────────────────────

    def _on_scrcpy_frame(
            self, target_id: str, bgr: np.ndarray,
            generation: int | None = None):
        """scrcpy 解码线程回调：通过 Qt 信号将帧转发到 UI 线程，并分叉喂给录屏器"""
        target = self._execution_targets.get(target_id)
        if (target is None or generation is not None
                and target.handle is not None
                and target.handle.binding().generation != generation):
            return
        if target is not None:
            target.last_capture = bgr
        if hasattr(self, "_scrcpy_frame_ready"):
            self._scrcpy_frame_ready.emit(target_id, bgr)
        # 录屏分叉：push 仅入队不阻塞解码线程，暂停/停止态内部直接丢弃。
        # 来源是开始录制时冻结的目标，不是当前预览目标——否则切换观察目标会
        # 把两台设备的画面静默拼进同一个视频。
        rec = self._screen_recorder
        if rec is not None and target_id == getattr(
                self, "_record_target_id", None):
            rec.push(bgr)

    def _on_scrcpy_frame_ui(self, target_id: str, bgr: np.ndarray):
        """UI 线程槽：更新预览区显示（由 _scrcpy_frame_ready 信号触发）

        预览隐藏时仅更新 _last_capture，跳过 BGR→RGB→QPixmap 转换以节省 CPU。
        """
        if target_id != self._execution_targets.active_target_id:
            return
        # 始终更新最新帧，供 capture() 使用
        self._last_capture = bgr
        # 预览不可见时跳过 UI 渲染
        if not self.preview_label.isVisible():
            return
        try:
            h, w_img = bgr.shape[:2]
            rgb = np.ascontiguousarray(bgr[:, :, ::-1])
            fmt = QImage.Format.Format_RGB888
            qimg = QImage(bytes(rgb.data), w_img, h, w_img * 3, fmt).copy()
            pixmap = QPixmap.fromImage(qimg)
            scaled = pixmap.scaled(
                self.preview_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            self.preview_label.setPixmap(scaled)
        except Exception as e:
            logger.debug(f"[scrcpy] 预览更新失败: {e}")

    @staticmethod
    def _identity_unconfirmed(target) -> bool:
        """该目标的身份是否只能靠 transport 地址区分。"""
        return target.kind == "adb" and not bool(
            target.metadata.get("device_identity_stable", True))

    def _unconfirmed_identity_conflict(self, target_id: str, stable: bool):
        """返回与待连接设备身份无法区分的已连接目标；没有冲突返回 None。

        同一个 target_id 的重连不算冲突：那就是同一个目标换了条 transport。
        """
        if stable:
            return None
        return next(
            (other for other in self._execution_targets.all()
             if other.id != target_id and self._identity_unconfirmed(other)),
            None,
        )

    @staticmethod
    def _release_rejected_connection(capture, agent) -> None:
        """释放一条被拒绝的连接已经建立的资源，不影响任何已登记目标。"""
        for name, resource, close in (
            ("截图后端", capture, "stop"), ("设备端代理", agent, "close"),
        ):
            if resource is None:
                continue
            try:
                getattr(resource, close)()
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"释放被拒绝连接的{name}失败: {exc}")

    def _abort_recording_for_target(self, target_id: str, reason: str) -> None:
        """只有被改动的目标正是录制来源时，才先安全结束当前录制。

        换绑截图后端或断开连接会让帧流中断，两次 binding 的帧不能静默拼成
        一个视频；但动的是别的目标时，正在录的那一路不该被牵连。
        """
        if target_id != getattr(self, "_record_target_id", None):
            return
        recorder = getattr(self, "_screen_recorder", None)
        abort_recording = getattr(self, "_abort_screen_record", None)
        if recorder is not None and callable(abort_recording):
            abort_recording(reason)

    def _rebind_target_resources(self, target) -> int:
        """资源换绑后同步 TargetHandle，返回新的资源代次。

        任务持有的是 TargetHandle 而不是裸后端引用，所以只改
        ``target.capture/input_ctrl`` 等于只改了 UI 投影：handle 仍指向上一次
        连接时的绑定，下一次启动会把已经 ``stop()`` 的后端喂给引擎。流式截图
        停掉后 ``capture()`` 仍返回最后一帧，错误不会报出来，工作流直接按几分钟
        前的画面点击，所以每处换绑都必须走这里。
        """
        if target.handle is None:
            from .execution_targets import TargetHandle
            target.handle = TargetHandle(target)
            return target.handle.binding().generation
        return target.handle.rebind(target).generation

    def _target_has_active_run(self, target_id: str) -> bool:
        """目标是否被运行实例占用；兼容独立测试宿主尚未提供 RunManager。"""
        manager = getattr(self, "_run_manager", None)
        if manager is None:
            return bool(getattr(self, "_running", False))
        return manager.run_for_target(target_id) is not None
