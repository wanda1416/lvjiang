"""窗口操作混入类 - 窗口扫描、定位、截屏、DPI 检测"""

import ctypes
from ctypes import wintypes

import numpy as np
from loguru import logger
from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QMenu, QTreeWidgetItem

from ...i18n import tr
from ..button_styles import apply_button_style, fit_button_width


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
            QComboBox,
            QDialog,
            QHBoxLayout,
            QLabel,
            QProgressBar,
            QPushButton,
            QVBoxLayout,
        )
        self._dialog = QDialog(parent)
        self._dialog.setWindowTitle(tr("未发现 ADB 设备"))
        self._dialog.setMinimumWidth(460)

        layout = QVBoxLayout(self._dialog)

        # 提示文字
        self._msg_label = QLabel(
            tr("未发现 USB 连接的 ADB 设备。\n\n"
               "可选择网段扫描局域网（设备需已开启无线调试），\n"
               "或直接扫描本机端口以发现模拟器。")
        )
        layout.addWidget(self._msg_label)

        # 网段选择（多网卡时避免只扫到虚拟网卡那一段）
        subnet_row = QHBoxLayout()
        subnet_row.addWidget(QLabel(tr("扫描网段：")))
        self._subnet_combo = QComboBox()
        self._subnet_combo.addItem(tr("全部网卡（默认）"), None)
        for iface in self._list_interfaces():
            self._subnet_combo.addItem(iface.label, [iface.subnet])
        subnet_row.addWidget(self._subnet_combo, 1)
        layout.addLayout(subnet_row)

        # 进度条（初始隐藏）
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)

        # 状态标签（初始隐藏）
        self._status_label = QLabel("")
        self._status_label.setVisible(False)
        self._status_label.setStyleSheet("color: gray;")
        layout.addWidget(self._status_label)

        # 按钮
        btn_layout = QHBoxLayout()
        self._scan_btn = QPushButton(tr("扫描局域网"))
        self._local_btn = QPushButton(tr("本地扫描（模拟器）"))
        self._cancel_btn = QPushButton(tr("取消"))
        apply_button_style(self._scan_btn)
        apply_button_style(self._local_btn, variant="neutral")
        apply_button_style(self._cancel_btn, variant="neutral")
        fit_button_width(self._scan_btn, self._local_btn, self._cancel_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(self._scan_btn)
        btn_layout.addWidget(self._local_btn)
        btn_layout.addWidget(self._cancel_btn)
        layout.addLayout(btn_layout)

        # 信号连接
        self._scan_btn.clicked.connect(self._on_scan_clicked)
        self._local_btn.clicked.connect(self._on_local_clicked)
        self._cancel_btn.clicked.connect(self._dialog.reject)

        # 回调
        self._on_scan_callback = None
        self._scan_started = False

    @staticmethod
    def _list_interfaces() -> list:
        """枚举本机网卡；失败时下拉框只保留「全部网卡」"""
        try:
            from ...core.android import list_ipv4_interfaces
            return list_ipv4_interfaces()
        except Exception as e:  # 枚举失败不该挡住扫描
            logger.warning(f"枚举本机网卡失败: {e}")
            return []

    def selected_subnets(self) -> list | None:
        """当前选中的网段前缀列表；None 表示全部网卡"""
        return self._subnet_combo.currentData()

    def _begin_scan(self, mode: str):
        """进入扫描中状态并回调上层"""
        self._scan_btn.setEnabled(False)
        self._local_btn.setEnabled(False)
        self._subnet_combo.setEnabled(False)
        self._cancel_btn.setText(tr("取消扫描"))
        self._cancel_btn.setEnabled(True)
        self._progress_bar.setVisible(True)
        self._status_label.setVisible(True)
        self._scan_started = True
        if self._on_scan_callback:
            self._on_scan_callback(mode, self.selected_subnets())

    def _on_scan_clicked(self):
        """点击「扫描局域网」"""
        self._scan_btn.setText(tr("扫描中..."))
        self._begin_scan("lan")

    def _on_local_clicked(self):
        """点击「本地扫描（模拟器）」"""
        self._local_btn.setText(tr("扫描中..."))
        self._begin_scan("local")

    def update_progress(self, message: str, current: int, total: int):
        """更新进度"""
        self._progress_bar.setMaximum(total)
        self._progress_bar.setValue(current)
        self._status_label.setText(message)

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

    def _sync_active_target_compat(self) -> None:
        """把当前执行目标投影到既有单目标字段。

        大量场景、采集和工作流入口仍通过这些字段访问后端；投影只发生在空闲期的
        显式目标切换，运行中目标列表被锁定，因此不会让正在执行的引擎改道。
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
        if target.resume_event is not None:
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
        if target.kind == "adb":
            target.capture_method = self._user_config.android_capture_method
        target.last_capture = self._last_capture

    @staticmethod
    def _target_connection_details(target) -> str:
        if target.kind == "adb":
            size = f"{target.width}×{target.height}" \
                if target.width and target.height else ""
            return " · ".join(part for part in (target.serial, size) if part)
        window = target.window or {}
        size = f"{window.get('width', 0)}×{window.get('height', 0)}"
        origin = f"({window.get('left', 0)}, {window.get('top', 0)})"
        return tr("起点 {origin} · {size}").format(origin=origin, size=size)

    @staticmethod
    def _target_status_details(target) -> str:
        if target.kind == "adb":
            return " · ".join(part for part in (
                target.capture_method,
                tr("设备端执行") if target.agent is not None else "ADB",
            ) if part)
        from ...core.desktop import WgcCapture
        input_mode = tr("后台模式") \
            if bool(getattr(target.input_ctrl, "background_mode", False)) \
            else tr("前台模式")
        capture_mode = tr("后台截图") \
            if isinstance(target.capture, WgcCapture) else tr("前台截图")
        return tr("{input_mode} · {capture_mode}").format(
            input_mode=input_mode,
            capture_mode=capture_mode,
        )

    def _target_details(self, target) -> str:
        """日志使用的完整摘要；UI 分列展示连接与状态。"""
        return " · ".join(part for part in (
            self._target_connection_details(target),
            self._target_status_details(target),
        ) if part)

    def _refresh_execution_targets_ui(self) -> None:
        tree = self.execution_target_list
        selected_id = self._execution_targets.active_target_id
        tree.blockSignals(True)
        tree.clear()
        selected_item = None
        for target in self._execution_targets.all():
            item = QTreeWidgetItem([
                target.display_name,
                tr("已连接") if target.ready else tr("已离线"),
                self._target_connection_details(target),
                self._target_status_details(target),
                "×",
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, target.id)
            item.setTextAlignment(4, Qt.AlignmentFlag.AlignCenter)
            item.setToolTip(
                4, tr("断开定位") if target.kind == "windows"
                else tr("断开连接"))
            tree.addTopLevelItem(item)
            if target.id == selected_id:
                selected_item = item
        if selected_item is not None:
            tree.setCurrentItem(selected_item)
        tree.resizeColumnToContents(0)
        tree.resizeColumnToContents(1)
        tree.blockSignals(False)

    @staticmethod
    def _execution_target_id_from_item(item) -> str:
        if item is None:
            return ""
        return str(item.data(0, Qt.ItemDataRole.UserRole) or "")

    def _on_execution_target_item_clicked(self, item, column: int) -> None:
        if column != 4:
            return
        self._disconnect_execution_target(
            self._execution_target_id_from_item(item))

    def _on_execution_target_context_menu(self, pos) -> None:
        item = self.execution_target_list.itemAt(pos)
        target_id = self._execution_target_id_from_item(item)
        target = self._execution_targets.get(target_id)
        if target is None:
            return
        menu = QMenu(self.execution_target_list)
        if target.kind == "windows":
            background_action = menu.addAction(tr("后台模式"))
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
            execution_action = menu.addAction(tr("设备端执行"))
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
        if self._running:
            self.log_text.append(tr("[提示] 任务运行中，执行目标已锁定"))
            self._refresh_execution_targets_ui()
            return
        self._execution_targets.select(str(target_id))
        self._sync_active_target_compat()
        self._refresh_active_target_ui()
        self._refresh_run_button()
        self.log_text.append(
            tr("[执行目标] 已切换到 {name}").format(
                name=self._active_execution_target().display_name))

    def _refresh_active_target_ui(self) -> None:
        target = self._active_execution_target()
        if target is None:
            self.preview_label.clear()
            self.preview_label.setText(tr("连接目标后可预览"))
            return
        if target.last_capture is not None:
            self._show_preview_image(target.last_capture)
        elif target.ready:
            self._capture_preview()

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
        if self._running:
            self.log_text.append(tr("[提示] 请先停止当前任务，再重新扫描窗口"))
            return

        self._apply_backend_ui("windows")

        self.btn_locate.setEnabled(False)
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
        self.btn_locate.setEnabled(True)
        self.statusBar().showMessage(tr("已扫描窗口 | 请下拉选择目标窗口并点击定位"))

    def _on_window_selected(self, index):
        """下拉框选择了某项时，启用定位按钮"""
        self.btn_locate.setEnabled(index >= 0)

    # ─── ADB 设备扫描/连接 ─────────────────────────────────

    def _on_scan_devices(self):
        """扫描 ADB 设备；只刷新候选，不断开已连接目标。"""
        if self._device_scan_running():
            self._cancel_device_scan()
            return
        if self._running:
            self.log_text.append(tr("[提示] 请先停止当前任务，再重新扫描设备"))
            return

        self._apply_backend_ui("adb")

        self.btn_locate.setEnabled(False)
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
        self.btn_locate.setEnabled(True)
        self.log_text.append(f"[扫描] 找到 {len(devices)} 台设备，请选择并点击连接")
        self.statusBar().showMessage(tr("已扫描设备 | 请选择设备并点击连接"))

    def _ask_wireless_scan(self):
        """询问用户是否扫描局域网 ADB 设备"""
        self._wireless_dialog = _WirelessScanDialog(self)
        # 延迟到下一轮事件循环显示模态对话框，确保调用栈已返回事件循环
        from PyQt6.QtCore import QTimer
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
        self.btn_locate.setEnabled(True)
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
        from .execution_targets import android_target_id
        existing = self._execution_targets.get(android_target_id(d["serial"]))
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
            self.btn_locate.setEnabled(False)
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
        target_id = android_target_id(combo_data["serial"])

        # scrcpy 模式下订阅帧回调，实现预览区实时视频流
        streaming = False
        if capture_method == "scrcpy":
            from ...core.android import AndroidStreamCapture
            if isinstance(capture, AndroidStreamCapture):
                capture.set_on_frame(
                    lambda frame, tid=target_id: self._on_scrcpy_frame(tid, frame))
                streaming = True
                logger.info("[连接] scrcpy 视频流预览已启用")

        # ── ADB 断连暂停恢复接线 ──
        resume_event = threading.Event()
        resume_event.set()
        device.resume_event = resume_event
        try:
            from ...core.app_controller import record_connected_android
            app_info = record_connected_android(device, width=w, height=h)
            if app_info.get("package"):
                self.log_text.append(
                    f"[应用识别] {app_info['package']}/{app_info['activity']}")
        except Exception as exc:  # noqa: BLE001 - 连接不应因前台应用探测失败而失败
            logger.warning(f"ADB 当前应用信息获取失败: {exc}")

        bridge = _AdbConnSignalBridge(target_id)
        bridge.adb_lost.connect(self._on_adb_connection_lost)
        device.on_connection_lost = bridge.notify_lost
        device.stop_check = lambda: self._stop_requested

        target = ExecutionTarget(
            id=target_id,
            kind="adb",
            display_name=(
                combo_data.get("model") or combo_data["serial"]),
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
        )
        old = self._execution_targets.put(target)
        if old is not None:
            self._dispose_execution_target(old)
        self._sync_active_target_compat()

        # 若工作流正阻塞在断连等待上（resume_event 未 set），
        # 把新的截图/输入后端同步给运行中的引擎，否则引擎继续用已死的旧 scrcpy 流截图
        if (self._running_target_id == target_id
                and not resume_event.is_set()):
            self._refresh_running_engine_backends()

        method_label = {"scrcpy": "scrcpy", "agent": "设备端截图"}.get(capture_method, "screencap")
        if agent is not None:
            method_label += "  |  " + tr("设备端执行")
        self.log_text.append(f"[连接成功] {combo_data['serial']} ({w}x{h}) [{method_label}]")
        hk = self._user_config.hotkeys
        self.statusBar().showMessage(self._hotkey_status(
            f"已连接设备 {combo_data['serial']}",
            (hk.start, tr("开始")), (hk.stop, tr("停止"))))
        if update_candidate_ui:
            self.btn_locate.setText(tr("连接"))
            self.btn_locate.setEnabled(True)
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
            self.btn_locate.setEnabled(True)

    def _stop_capture_backend(self):
        """停止并丢弃当前截图后端（桌面/ADB/scrcpy 共用）。"""
        if self._capture is None:
            return
        try:
            self._capture.stop()
        except Exception as e:
            logger.debug(f"截图后端停止失败: {e}")
        finally:
            self._capture = None

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

    def _teardown_adb_backend(self):
        """兼容入口：清理全部 Android 目标。"""
        # 录屏进行中/待保存时先自动转正保存，再停止截图后端
        if self._screen_recorder is not None:
            self._abort_screen_record(tr("断连"))
        ids = [target.id for target in self._execution_targets.all()
               if target.kind == "adb"]
        for target_id in ids:
            target = self._execution_targets.remove(target_id)
            if target is not None:
                self._dispose_execution_target(target)
        self._sync_active_target_compat()
        if hasattr(self, "execution_target_list"):
            self._refresh_execution_targets_ui()
        # 停止后台扫描/连接线程
        self._wait_device_thread()

    def _refresh_bg_mode_lock(self):
        """任务运行状态变化后刷新连接草稿的可编辑状态。"""
        self._refresh_connection_draft_ui(self._candidate_backend)

    def _refresh_bg_capture_visibility(self, mode: str | None = None):
        """兼容入口：刷新 Windows 连接草稿控件。"""
        self._refresh_connection_draft_ui(mode or self._candidate_backend)

    def _apply_bg_capture_default(self):
        """后台模式启用时，把配置默认值写入连接草稿。"""
        if self._user_config.desktop_background_capture:
            self._window_connection_draft.background_capture = True
        self._refresh_connection_draft_ui("windows")

    def _on_disconnect(self):
        """兼容入口：断开当前执行目标。"""
        target = self._active_execution_target()
        self._disconnect_execution_target(target.id if target is not None else "")

    def _disconnect_execution_target(self, target_id: str) -> None:
        """按稳定 ID 断开一个目标，不影响其他连接。"""
        target = self._execution_targets.get(target_id)
        if target is None:
            return
        if self._running:
            self.log_text.append(tr("[提示] 任务运行中不能断开执行目标"))
            return
        removed = self._execution_targets.remove(target.id)
        if removed is not None:
            self._dispose_execution_target(removed)
        if target.kind == "windows":
            self._red_box_flash_timer.stop()
            self._overlay.hide_border()
        from ...core.app_controller import remove_connected_target
        remove_connected_target(target.id)
        self._sync_active_target_compat()
        self._refresh_execution_targets_ui()
        self._refresh_active_target_ui()
        self.statusBar().showMessage(
            tr("已断开目标：{name}").format(name=target.display_name))
        self.log_text.append(f"[断连] {target.display_name}")
        self._refresh_run_button()

    def _refresh_or_reconnect_execution_target(self, target_id: str) -> None:
        """刷新窗口坐标，或重新建立指定 Android 目标。"""
        target = self._execution_targets.get(target_id)
        if target is None:
            return
        if self._running:
            self.log_text.append(tr("[提示] 任务运行中不能刷新或重连目标"))
            return
        if target.kind == "adb":
            self._reconnect_android_target(target_id)
            return

        window = target.window
        if window is None:
            return
        self._refresh_window_rect(window)
        target.width = int(window.get("width") or 0)
        target.height = int(window.get("height") or 0)
        if target.capture is not None:
            target.capture.set_capture_region(
                int(window.get("left") or 0),
                int(window.get("top") or 0),
                target.width,
                target.height,
            )
        if self.chk_red_box.isChecked():
            self._overlay.show_border(
                int(window.get("left") or 0),
                int(window.get("top") or 0),
                target.width,
                target.height,
            )
        from ...core.app_controller import record_connected_window
        record_connected_window(window)
        if self._execution_targets.active_target_id == target.id:
            self._sync_active_target_compat()
            self._capture_preview()
        self._refresh_execution_targets_ui()
        self.statusBar().showMessage(tr("已刷新窗口位置"))
        self.log_text.append(
            tr("[定位刷新] {name} · {details}").format(
                name=target.display_name,
                details=self._target_details(target)))

    def _set_window_target_background_input(
            self, target_id: str, enabled: bool) -> None:
        """只修改指定窗口目标的实际输入后端。"""
        target = self._execution_targets.get(target_id)
        if target is None or target.kind != "windows" or target.window is None:
            return
        if self._running:
            self.log_text.append(tr("[提示] 任务运行中不能切换输入方式"))
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
                input_sim=self._user_config.input_sim)
        target.input_kind = str(
            getattr(target.input_ctrl, "kind", "") or "")
        if self._execution_targets.active_target_id == target_id:
            self._sync_active_target_compat()
        self._refresh_execution_targets_ui()
        self.log_text.append(
            tr("[模式] {name} 已切换到{mode}").format(
                name=target.display_name,
                mode=tr("后台模式") if enabled else tr("前台模式")))

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
        if self._running:
            self.log_text.append(tr("[提示] 任务运行中不能切换截图方式"))
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
        if self._running:
            self.log_text.append(tr("[提示] 任务运行中不能切换截图方式"))
            return
        method = "scrcpy" if enabled else "screencap"
        from ...core.android import AndroidStreamCapture, create_capture_backend
        capture = create_capture_backend(device=target.device, method=method)
        if not capture.start():
            self.log_text.append(
                tr("[错误] {method} 截图后端不可用，保留原截图方式").format(
                    method=method))
            return
        streaming = False
        if method == "scrcpy" and isinstance(capture, AndroidStreamCapture):
            capture.set_on_frame(
                lambda frame, tid=target_id: self._on_scrcpy_frame(tid, frame))
            streaming = True
        old_capture = target.capture
        target.capture = capture
        target.capture_method = method
        target.streaming = streaming
        target.last_capture = None
        if old_capture is not None:
            try:
                old_capture.stop()
            except Exception as exc:
                logger.debug(f"停止旧截图后端时报错（忽略）: {exc}")
        if self._execution_targets.active_target_id == target_id:
            self._sync_active_target_compat()
            if not streaming:
                self._capture_preview()
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
            {"serial": target.serial, "model": target.display_name},
            capture_method=target.capture_method or "screencap",
            device_execution=device_execution,
            update_candidate_ui=False,
        )

    def _device_combo_current_serial(self) -> str:
        """获取当前下拉框中的设备 serial（用于日志）"""
        d = self.window_combo.currentData()
        return d.get("serial", "?") if d else "?"

    # ─── 窗口定位 ──────────────────────────────────────────

    def _on_locate_window(self):
        """连接当前候选；窗口单例替换，Android 按 serial 累加。"""
        if self._candidate_backend == "adb":
            self._on_connect_device()
            return
        w = self.window_combo.currentData()
        if not w:
            return
        self._refresh_window_rect(w)
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
        from .execution_targets import WINDOW_TARGET_ID, ExecutionTarget
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
            input_ctrl = SendInputInput(input_sim=self._user_config.input_sim)
        return ExecutionTarget(
            id=WINDOW_TARGET_ID,
            kind="windows",
            display_name=str(w.get("title") or tr("游戏窗口")),
            capture=capture,
            input_ctrl=input_ctrl,
            input_kind=str(getattr(input_ctrl, "kind", "") or ""),
            window=w,
            width=int(w.get("width") or 0),
            height=int(w.get("height") or 0),
            capture_method="wgc" if actual_bg_capture else "mss",
        )

    def _hide_red_box_after_locate(self):
        """未勾选标定时，定位成功的红框提示只显示一秒。"""
        if (
            self._backend == "windows"
            and self._target_window is not None
            and not self.chk_red_box.isChecked()
        ):
            self._overlay.hide_border()

    def _on_red_box_changed(self, state):
        """红框标定仅控制本次运行中的持续显示，不写入用户配置。"""
        self._red_box_flash_timer.stop()
        if bool(state):
            if self._target_window is not None:
                w = self._target_window
                self._overlay.show_border(w['left'], w['top'], w['width'], w['height'])
                self._overlay.set_color("red")
        else:
            self._overlay.hide_border()

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
        label = tr("设备端执行（需安装律匠 App）") if state else "ADB shell input"
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

    def _on_target_window_rebound(self, window: dict):
        """工作流重启客户端后回调：把定位状态挪到新窗口上。

        运行在工作流线程，因此只改纯数据 `_target_window`，不碰任何控件；
        预览、场景编辑器和下一次运行都从它取值，不然定位状态会一直停在
        已经销毁的句柄上，直到用户手动重新定位。
        """
        target = self._target_window
        if not target:
            return
        for key in ("hwnd", "pid", "title", "executable",
                    "left", "top", "width", "height"):
            if window.get(key) is not None:
                target[key] = window[key]
        logger.info(
            f"[定位跟随] 客户端已重启，跟随到新窗口 hwnd={target.get('hwnd')}")

    # ─── Win32 工具 ───────────────────────────────────────

    def _refresh_window_rect(self, w: dict):
        """通过 Win32 GetWindowRect 实时刷新窗口位置。"""
        rect = wintypes.RECT()
        if ctypes.windll.user32.GetWindowRect(wintypes.HWND(w['hwnd']), ctypes.byref(rect)):
            w['left'] = rect.left
            w['top'] = rect.top
            w['width'] = rect.right - rect.left
            w['height'] = rect.bottom - rect.top

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

    def _on_scrcpy_frame(self, target_id: str, bgr: np.ndarray):
        """scrcpy 解码线程回调：通过 Qt 信号将帧转发到 UI 线程，并分叉喂给录屏器"""
        target = self._execution_targets.get(target_id)
        if target is not None:
            target.last_capture = bgr
        if hasattr(self, "_scrcpy_frame_ready"):
            self._scrcpy_frame_ready.emit(target_id, bgr)
        # 录屏分叉：push 仅入队不阻塞解码线程，暂停/停止态内部直接丢弃
        rec = self._screen_recorder
        if rec is not None and target_id == self._execution_targets.active_target_id:
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
