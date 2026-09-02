"""窗口操作混入类 - 窗口扫描、定位、截屏、DPI 检测"""

import ctypes
from ctypes import wintypes

import numpy as np
from loguru import logger
from PyQt6.QtCore import QObject, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap

from ...i18n import tr
from ..button_styles import apply_button_style, fit_button_width


class _AdbConnSignalBridge(QObject):
    """工作流线程 → 主线程的 ADB 断连信号桥"""
    adb_lost = pyqtSignal(str)

    def notify_lost(self, error_msg: str):
        self.adb_lost.emit(error_msg)


class _DeviceWorker(QObject):
    """后台线程：扫描或连接 ADB 设备（互斥，不会同时运行）"""
    scan_finished = pyqtSignal(list)  # devices
    wireless_finished = pyqtSignal(list)  # devices (无线扫描结果)
    wireless_progress = pyqtSignal(str, int, int)  # message, current, total
    connect_finished = pyqtSignal(object, object, str, int, int, object)  # device, capture, method, w, h, agent
    notice = pyqtSignal(str)  # 连接过程中的提示（主线程写进日志区）
    error = pyqtSignal(str)

    def __init__(self, task: str, serial: str = "", capture_method: str = "", agent_mode: bool = False):
        super().__init__()
        self._task = task
        self._serial = serial
        self._capture_method = capture_method
        self._agent_mode = agent_mode
        self._cancelled = False

    def cancel(self):
        """取消后台任务"""
        self._cancelled = True

    def run(self):
        try:
            if self._task == "scan":
                self._do_scan()
            elif self._task == "wireless_scan":
                self._do_wireless_scan()
            else:
                self._do_connect()
        except Exception as e:
            if not self._cancelled:
                self.error.emit(str(e))

    def _do_scan(self):
        from ...core.android import list_adb_devices
        devices = list_adb_devices()
        if not self._cancelled:
            self.scan_finished.emit(devices)

    def _do_wireless_scan(self):
        from ...core.android import scan_and_connect_wireless

        def progress_cb(message: str, current: int, total: int):
            if self._cancelled:
                raise RuntimeError("cancelled")
            self.wireless_progress.emit(message, current, total)

        try:
            devices = scan_and_connect_wireless(progress_cb=progress_cb)
            if not self._cancelled:
                self.wireless_finished.emit(devices)
        except RuntimeError as e:
            if str(e) != "cancelled":
                raise

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
                self.notice.emit(tr("[设备端手势] App 不可达或设备端输入通道未就绪，回退 adb shell input"))
            else:
                self.notice.emit(f"[设备端手势] 已连接 {agent.describe()}")

        capture = create_capture_backend(device=device, method=method)
        started = capture.start()
        if not started:
            if agent is not None:
                agent.close()
            self.error.emit(f"{method} 截图后端不可用")
            return

        self.connect_finished.emit(device, capture, method, w, h, agent)


class _WirelessScanDialog(QObject):
    """局域网扫描对话框：带进度条和状态反馈"""

    def __init__(self, parent):
        super().__init__(parent)
        from PyQt6.QtWidgets import (
            QDialog,
            QHBoxLayout,
            QLabel,
            QProgressBar,
            QPushButton,
            QVBoxLayout,
        )
        self._dialog = QDialog(parent)
        self._dialog.setWindowTitle(tr("未发现 ADB 设备"))
        self._dialog.setMinimumWidth(400)

        layout = QVBoxLayout(self._dialog)

        # 提示文字
        self._msg_label = QLabel(
            tr("未发现 USB 连接的 ADB 设备。\n\n"
               "是否扫描局域网并尝试无线连接？\n"
               "（设备需已开启无线调试）")
        )
        layout.addWidget(self._msg_label)

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
        self._scan_btn = QPushButton(tr("扫描"))
        self._cancel_btn = QPushButton(tr("取消"))
        apply_button_style(self._scan_btn)
        apply_button_style(self._cancel_btn, variant="neutral")
        fit_button_width(self._scan_btn, self._cancel_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(self._scan_btn)
        btn_layout.addWidget(self._cancel_btn)
        layout.addLayout(btn_layout)

        # 信号连接
        self._scan_btn.clicked.connect(self._on_scan_clicked)
        self._cancel_btn.clicked.connect(self._dialog.reject)

        # 回调
        self._on_scan_callback = None

    def _on_scan_clicked(self):
        """点击扫描按钮"""
        self._scan_btn.setEnabled(False)
        self._scan_btn.setText(tr("扫描中..."))
        self._cancel_btn.setEnabled(False)
        self._progress_bar.setVisible(True)
        self._status_label.setVisible(True)
        if self._on_scan_callback:
            self._on_scan_callback()

    def update_progress(self, message: str, current: int, total: int):
        """更新进度"""
        self._progress_bar.setMaximum(total)
        self._progress_bar.setValue(current)
        self._status_label.setText(message)

    def exec(self, on_scan_callback) -> bool:
        """显示对话框，返回是否点击了扫描"""
        from PyQt6.QtWidgets import QDialog
        self._on_scan_callback = on_scan_callback
        return self._dialog.exec() == QDialog.DialogCode.Accepted

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
        _layout_manager, _running, btn_locate, lbl_window_info, window_combo,
        preview_label, log_text, statusBar(), _refresh_run_button()
    scrcpy 帧信号:
        主类需定义 _scrcpy_frame_ready = pyqtSignal(object) 并连接 _on_scrcpy_frame_ui
    """

    # 流式截图态的类级兜底：连接成功前被读也有明确默认值
    _scrcpy_streaming = False

    # ─── 后端模式切换 ──────────────────────────────────────

    def _apply_backend_ui(self, mode: str):
        """根据当前后端模式调整定位按钮文案。
        两个扫描按钮始终保留，用户点哪个即切到哪个模式。
        """
        if mode == "adb":
            self.btn_locate.setText(tr("连接"))
        else:
            self.btn_locate.setText(tr("定位"))

    # ─── 窗口扫描 ──────────────────────────────────────────

    def _on_scan_window(self):
        """扫描所有可见窗口，填充列表（切换到 Windows 投屏模式）"""
        from ...core.platforms import DESKTOP_BACKEND_AVAILABLE
        if not DESKTOP_BACKEND_AVAILABLE:
            # 按钮在非 Windows 已隐藏，此处为防御：投屏模式依赖 Win32 API
            self.log_text.append(tr("[提示] 当前平台不支持窗口投屏模式，请使用「扫描设备」"))
            return
        if self._running:
            self.log_text.append(tr("[提示] 请先停止当前任务，再重新扫描窗口"))
            return

        # 切到 windows 模式：清理可能存在的 ADB 资源，恢复 Windows 输入控制器
        self._teardown_adb_backend()
        self._set_connected_ui(False)
        self._backend = "windows"
        self._input = self._win_input
        self._apply_backend_ui("windows")

        # 显示后台模式开关，隐藏流式截图开关
        if hasattr(self, "chk_bg_mode"):
            if not self.chk_bg_mode.isVisible():
                # 首次进入 Windows 模式，从配置读取初始状态
                self.chk_bg_mode.blockSignals(True)
                self.chk_bg_mode.setChecked(self._user_config.desktop_background_input)
                self.chk_bg_mode.blockSignals(False)
            self.chk_bg_mode.setVisible(True)
            self.chk_bg_mode.setEnabled(True)
        # 红框标定随后台模式一起显示；不读/写配置，checkbox 自身的默认勾选态即初始态
        if hasattr(self, "chk_red_box"):
            self.chk_red_box.setVisible(True)
            self.chk_red_box.setEnabled(True)
        if hasattr(self, "chk_scrcpy"):
            self.chk_scrcpy.setVisible(False)
        if hasattr(self, "chk_agent"):
            self.chk_agent.setVisible(False)

        had_target = self._target_window is not None
        self._target_window = None
        self._overlay.hide_border()
        self.btn_locate.setEnabled(False)
        self.lbl_window_info.setText(tr("未定位窗口"))
        self.lbl_window_info.setStyleSheet("color: gray;")
        self.statusBar().showMessage(tr("正在扫描窗口..."))
        self._refresh_run_button()
        if had_target:
            self.log_text.append(tr("[状态] 重新扫描窗口，旧定位已失效"))

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
            for i, w in enumerate(self._scanned_windows):
                if keyword in w["title"]:
                    self.window_combo.setCurrentIndex(i)
                    self._on_locate_window()
                    self.log_text.append(f"[扫描] 已自动匹配窗口: {w['title']}（关键字: {keyword}）")
                    return
            self.log_text.append(f"[扫描] 找到 {len(self._scanned_windows)} 个窗口，未匹配到关键字「{keyword}」")
        else:
            self.log_text.append(f"[扫描] 找到 {len(self._scanned_windows)} 个窗口，请下拉选择目标窗口")
        self.btn_locate.setEnabled(True)
        self.lbl_window_info.setText(tr("请下拉选择目标窗口..."))
        self.lbl_window_info.setStyleSheet("color: orange;")
        self.statusBar().showMessage(tr("已扫描窗口 | 请下拉选择目标窗口并点击定位"))

    def _on_window_selected(self, index):
        """下拉框选择了某项时，启用定位按钮"""
        self.btn_locate.setEnabled(index >= 0)

    # ─── ADB 设备扫描/连接 ─────────────────────────────────

    def _on_scan_devices(self):
        """扫描已连接（device 状态）的设备，填充下拉框（切换到 ADB 设备模式）"""
        if self._running:
            self.log_text.append(tr("[提示] 请先停止当前任务，再重新扫描设备"))
            return

        # 切到 adb 模式：清理旧 ADB 资源与 Windows 定位状态
        self._teardown_adb_backend()
        self._set_connected_ui(False)
        self._backend = "adb"
        self._apply_backend_ui("adb")

        # 显示流式截图开关，隐藏后台模式开关
        if hasattr(self, "chk_scrcpy"):
            if not self.chk_scrcpy.isVisible():
                # 首次进入 ADB 模式，从配置读取初始状态
                is_scrcpy = self._user_config.android_capture_method == "scrcpy"
                self.chk_scrcpy.blockSignals(True)
                self.chk_scrcpy.setChecked(is_scrcpy)
                self.chk_scrcpy.blockSignals(False)
            self.chk_scrcpy.setVisible(True)
            self.chk_scrcpy.setEnabled(True)
        if hasattr(self, "chk_agent"):
            if not self.chk_agent.isVisible():
                self.chk_agent.blockSignals(True)
                self.chk_agent.setChecked(
                    self._user_config.android_input_method == "device_gesture")
                self.chk_agent.blockSignals(False)
            self.chk_agent.setVisible(True)
            self.chk_agent.setEnabled(True)
        if hasattr(self, "chk_bg_mode"):
            self.chk_bg_mode.setVisible(False)
        if hasattr(self, "chk_red_box"):
            self.chk_red_box.setVisible(False)
        if self._target_window is not None:
            self._target_window = None
            self._overlay.hide_border()

        self._device_ready = False
        self.btn_locate.setEnabled(False)
        self.btn_scan_device.setEnabled(False)
        self.btn_scan_device.setText(tr("扫描中..."))
        self.lbl_window_info.setText(tr("未连接设备"))
        self.lbl_window_info.setStyleSheet("color: gray;")
        self.statusBar().showMessage(tr("正在扫描设备..."))
        self._refresh_run_button()

        # 异步扫描
        self._wait_device_thread()
        self._device_thread = QThread()
        self._device_worker = _DeviceWorker(task="scan")
        self._device_worker.moveToThread(self._device_thread)
        self._device_thread.started.connect(self._device_worker.run)
        self._device_worker.scan_finished.connect(self._on_scan_devices_done)
        self._device_worker.error.connect(self._on_scan_devices_error)
        self._device_worker.scan_finished.connect(self._device_thread.quit)
        self._device_worker.error.connect(self._device_thread.quit)
        self._device_thread.start()

    def _on_scan_devices_done(self, devices: list):
        """扫描完成回调（主线程）"""
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
        self.lbl_window_info.setText(tr("请下拉选择设备并点击连接..."))
        self.lbl_window_info.setStyleSheet("color: orange;")
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
            # 用户取消 — 停止后台线程，防止过期回调更新 UI
            self._cancel_wireless_scan()
            self.btn_scan_device.setEnabled(True)
            self.btn_scan_device.setText(tr("扫描设备"))
            self.statusBar().showMessage(tr("已取消扫描"))

    def _cancel_wireless_scan(self):
        """取消无线扫描并等待线程结束"""
        if hasattr(self, '_device_worker') and self._device_worker:
            self._device_worker.cancel()
        if hasattr(self, '_device_thread') and self._device_thread and self._device_thread.isRunning():
            self._device_thread.quit()
            self._device_thread.wait(5000)  # 等待线程结束，最多 5 秒

    def _wait_device_thread(self):
        """等待可能存在的旧设备线程退出"""
        if hasattr(self, '_device_thread') and self._device_thread.isRunning():
            self._device_thread.quit()
            self._device_thread.wait(3000)

    def _start_wireless_scan(self):
        """启动局域网 ADB 扫描（异步）"""
        self.btn_scan_device.setEnabled(False)
        self.btn_scan_device.setText(tr("扫描局域网..."))
        self.statusBar().showMessage(tr("正在扫描局域网..."))
        self.log_text.append(tr("[扫描] 正在扫描局域网 ADB 设备..."))

        self._wait_device_thread()
        self._device_thread = QThread()
        self._device_worker = _DeviceWorker(task="wireless_scan")
        self._device_worker.moveToThread(self._device_thread)
        self._device_thread.started.connect(self._device_worker.run)
        self._device_worker.wireless_finished.connect(self._on_wireless_scan_done)
        self._device_worker.wireless_progress.connect(self._on_wireless_scan_progress)
        self._device_worker.error.connect(self._on_wireless_scan_error)
        self._device_worker.wireless_finished.connect(self._device_thread.quit)
        self._device_worker.error.connect(self._device_thread.quit)
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
        if hasattr(self, '_device_worker') and self._device_worker and self._device_worker._cancelled:
            return

        from PyQt6.QtWidgets import QMessageBox
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
            self.log_text.append(tr("[扫描] 局域网内未发现可连接的 ADB 设备"))
            self.statusBar().showMessage(tr("未发现设备 | 请确认设备已开启无线调试"))
            QMessageBox.warning(
                self,  # type: ignore[arg-type]  # mixin: self is QWidget
                tr("未发现设备"),
                tr("局域网内未发现可连接的 ADB 设备。\n\n"
                   "请确认：\n"
                   "1. 设备与电脑在同一局域网\n"
                   "2. 设备已开启无线调试（开发者选项）"),
            )
            return

        self._scanned_windows = devices
        for d in devices:
            label = d["serial"] + (f"  ({d['model']})" if d.get("model") else "")
            self.window_combo.addItem(label, d)
        self.btn_locate.setEnabled(True)
        self.lbl_window_info.setText(f"已发现 {len(devices)} 台设备，请选择并点击连接")
        self.lbl_window_info.setStyleSheet("color: green;")
        self.log_text.append(f"[扫描] 局域网发现 {len(devices)} 台设备，请选择并点击连接")
        self.statusBar().showMessage(f"已发现 {len(devices)} 台设备 | 请选择并点击连接")

    def _on_wireless_scan_error(self, error_msg: str):
        """局域网扫描失败回调（主线程）"""
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
        self.btn_scan_device.setEnabled(True)
        self.btn_scan_device.setText(tr("扫描设备"))
        logger.error(f"扫描设备失败: {error_msg}")
        self.log_text.append(f"[错误] 扫描设备失败: {error_msg}")
        self.statusBar().showMessage(tr("扫描失败 | 详见日志"))

    def _on_connect_device(self):
        """ADB 模式：异步连接选中设备（adb shell input + adb screencap/scrcpy）"""
        d = self.window_combo.currentData()
        if not d:
            return

        # 若已连接旧设备，先清理资源
        self._teardown_adb_backend()

        # UI 进入连接中状态
        self.btn_locate.setEnabled(False)
        self.btn_locate.setText(tr("连接中..."))
        self.statusBar().showMessage(tr("正在连接设备..."))

        capture_method = self._user_config.android_capture_method

        # 异步连接
        self._wait_device_thread()
        self._device_thread = QThread()
        self._device_worker = _DeviceWorker(
            task="connect", serial=d["serial"], capture_method=capture_method,
            agent_mode=self._user_config.android_input_method == "device_gesture",
        )
        self._device_worker.moveToThread(self._device_thread)
        self._device_thread.started.connect(self._device_worker.run)
        self._device_worker.notice.connect(self.log_text.append)
        self._device_worker.connect_finished.connect(
            lambda device, capture, method, w, h, agent: self._on_connect_done(d, device, capture, method, w, h, agent)
        )
        self._device_worker.error.connect(self._on_connect_error)
        self._device_worker.connect_finished.connect(self._device_thread.quit)
        self._device_worker.error.connect(self._device_thread.quit)
        self._device_thread.start()

    def _on_connect_done(self, combo_data, device, capture, capture_method, w, h, agent=None):
        """连接成功回调（主线程）"""
        from ...core.android import create_input_backend

        # 创建输入控制器：有设备端代理走无障碍手势，否则 adb shell input
        self._agent = agent
        self._input = create_input_backend(device=device, input_sim=self._user_config.input_sim, agent=agent)

        # scrcpy 模式下订阅帧回调，实现预览区实时视频流
        self._scrcpy_streaming = False
        if capture_method == "scrcpy":
            from ...core.android import AndroidStreamCapture
            if isinstance(capture, AndroidStreamCapture):
                capture.set_on_frame(self._on_scrcpy_frame)
                self._scrcpy_streaming = True
                logger.info("[连接] scrcpy 视频流预览已启用")

        # ── ADB 断连暂停恢复接线 ──
        # resume_event 存在主窗口级别（self._adb_resume_event），不随 device 断连/重连而丢失。
        # 每次连接都指向同一个 event，确保工作流线程等待的和「恢复」按钮 set 的是同一个。
        device.resume_event = self._adb_resume_event

        self._capture = capture
        self._device = device
        self._device_ready = True

        self._adb_conn_bridge = _AdbConnSignalBridge()
        self._adb_conn_bridge.adb_lost.connect(self._on_adb_connection_lost)
        device.on_connection_lost = self._adb_conn_bridge.notify_lost
        device.stop_check = lambda: self._stop_requested

        # 若工作流正阻塞在断连等待上（resume_event 未 set），
        # 把新的截图/输入后端同步给运行中的引擎，否则引擎继续用已死的旧 scrcpy 流截图
        resume_event = getattr(self, '_adb_resume_event', None)
        if resume_event is not None and not resume_event.is_set():
            self._refresh_running_engine_backends()

        method_label = {"scrcpy": "scrcpy", "agent": "设备端截图"}.get(capture_method, "screencap")
        if agent is not None:
            method_label += "  |  " + tr("设备端手势")
        self.lbl_window_info.setText(f"已连接: {combo_data['serial']}  |  分辨率: {w}x{h}  |  {method_label}")
        self.lbl_window_info.setStyleSheet("color: green;")
        self.log_text.append(f"[连接成功] {combo_data['serial']} ({w}x{h}) [{method_label}]")
        hk = self._user_config.hotkeys
        self.statusBar().showMessage(
            f"已连接设备 {combo_data['serial']} | {hk.start} {tr('开始')} | {hk.stop} {tr('停止')}")
        self.btn_locate.setText(tr("断连"))
        self.btn_locate.setEnabled(True)
        self._set_connected_ui(True)
        self._refresh_run_button()
        # screencap 模式手动刷新预览；scrcpy 模式自动推帧
        if not self._scrcpy_streaming:
            self._capture_preview()

    def _on_connect_error(self, error_msg: str):
        """连接失败回调（主线程）"""
        logger.error(f"连接设备失败: {error_msg}")
        self.log_text.append(f"[错误] 连接设备失败: {error_msg}")
        self.statusBar().showMessage(tr("连接失败 | 详见日志"))
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

    def _teardown_adb_backend(self):
        """清理 ADB 后端资源，用于重连或退出（仅清理资源，不改 UI）"""
        # 录屏进行中/待保存时先自动转正保存，再停止截图后端
        if self._screen_recorder is not None:
            self._abort_screen_record(tr("断连"))
        self._device_ready = False
        self._scrcpy_streaming = False
        self._stop_capture_backend()
        self._input = None
        # 设备端代理：关 socket + 撤 adb forward
        agent = getattr(self, "_agent", None)
        if agent is not None:
            try:
                agent.close()
            except Exception as e:
                logger.debug(f"[设备端手势] 关闭代理失败: {e}")
            self._agent = None
        # 清理断连信号桥
        if hasattr(self, '_adb_conn_bridge') and self._adb_conn_bridge is not None:
            self._adb_conn_bridge.deleteLater()
            self._adb_conn_bridge = None
        self._device = None
        # 停止后台扫描/连接线程
        self._wait_device_thread()

    def _set_connected_ui(self, connected: bool):
        """连接/断连后统一更新 UI 控件可用性"""
        self.btn_scan_window.setEnabled(not connected)
        self.btn_scan_device.setEnabled(not connected)
        self.window_combo.setEnabled(not connected)
        if connected:
            # 连接/定位后锁定 ADB 侧 checkbox，防止误切换。
            # 后台模式不在此一并锁死：Windows 定位后允许再次切换（见 _refresh_bg_mode_lock），
            # 避免每次切换都要断连重新定位；它只在任务运行期间被锁定。
            if hasattr(self, "chk_scrcpy"):
                self.chk_scrcpy.setEnabled(False)
            if hasattr(self, "chk_agent"):
                self.chk_agent.setEnabled(False)
        else:
            # 断连后恢复当前模式可见 checkbox 的可选状态
            if hasattr(self, "chk_bg_mode") and self.chk_bg_mode.isVisible():
                self.chk_bg_mode.setEnabled(True)
            if hasattr(self, "chk_scrcpy") and self.chk_scrcpy.isVisible():
                self.chk_scrcpy.setEnabled(True)
            if hasattr(self, "chk_agent") and self.chk_agent.isVisible():
                self.chk_agent.setEnabled(True)
        self._refresh_bg_mode_lock()
        # 采集面板（录屏/截屏）随连接态刷新可用性
        if hasattr(self, "_apply_rec_state"):
            self._apply_rec_state()

    def _refresh_bg_mode_lock(self):
        """刷新"后台模式"开关的可用性。

        Windows 定位后仍允许再次切换后台/前台输入模式，无需断连重新定位；
        只在任务运行（含暂停）期间锁定，任务结束后自动恢复——避免每次切换
        都要走一遍断连 + 重新定位。未定位状态由调用方直接控制可用性，这里
        不覆盖。
        """
        if not hasattr(self, "chk_bg_mode") or not self.chk_bg_mode.isVisible():
            return
        if self._backend == "windows" and self._target_window is not None:
            self.chk_bg_mode.setEnabled(not self._running)

    def _on_disconnect(self):
        """通用断连：根据后端模式清理资源并恢复 UI"""
        if self._backend == "adb":
            serial = self._device_combo_current_serial()
            self._teardown_adb_backend()
            self._set_connected_ui(False)
            self.btn_locate.setText(tr("连接"))
            self.lbl_window_info.setText(tr("已断开连接"))
            self.lbl_window_info.setStyleSheet("color: gray;")
            self.preview_label.setText(tr("预览已停止"))
            self.statusBar().showMessage(tr("已断开设备连接"))
            self.log_text.append(f"[断连] 设备已断开: {serial}")
        else:
            # Windows 模式：清除定位状态，但保留窗口选择
            self._target_window = None
            self._overlay.hide_border()
            self._stop_capture_backend()
            self._set_connected_ui(False)
            self.btn_locate.setText(tr("定位"))
            self.lbl_window_info.setText(tr("未定位窗口"))
            self.lbl_window_info.setStyleSheet("color: gray;")
            self.statusBar().showMessage(tr("已取消窗口定位"))
            self.log_text.append(tr("[断连] 窗口定位已清除"))
        self._refresh_run_button()

    def _device_combo_current_serial(self) -> str:
        """获取当前下拉框中的设备 serial（用于日志）"""
        d = self.window_combo.currentData()
        return d.get("serial", "?") if d else "?"

    # ─── 窗口定位 ──────────────────────────────────────────

    def _on_locate_window(self):
        """定位选中的窗口 / 连接设备；已连接/定位后变为断连"""
        if self._device_ready or self._target_window is not None:
            # 已连接/已定位 → 断连
            self._on_disconnect()
            return
        if self._backend == "adb":
            self._on_connect_device()
            return
        w = self.window_combo.currentData()
        if not w:
            return
        self._refresh_window_rect(w)
        self._target_window = w

        ratio = self._get_window_dpi_ratio(w["hwnd"])
        logger.info(
            f"目标窗口 Win32原始: ({w['left']},{w['top']},{w['width']}x{w['height']})"
            f" DPI={ratio}"
        )

        self.lbl_window_info.setText(
            f"已定位: {w['title']}  |  "
            f"位置: ({w['left']}, {w['top']})  大小: {w['width']}x{w['height']}"
            + (f"  DPI缩放: {ratio:.1f}x" if ratio != 1.0 else "")
        )
        self.lbl_window_info.setStyleSheet("color: green;")
        self.log_text.append(
            f"[定位成功] {w['title']}  "
            f"({w['width']}x{w['height']} @ {w['left']},{w['top']})"
            + (f" DPI={ratio:.1f}x" if ratio != 1.0 else "")
        )
        if not hasattr(self, "chk_red_box") or self.chk_red_box.isChecked():
            self._overlay.show_border(w['left'], w['top'], w['width'], w['height'])
            self._overlay.set_color("red")
        self._set_connected_ui(True)
        self.btn_locate.setText(tr("断连"))
        self._refresh_run_button()
        self._capture_preview()

        # 定位成功后根据 checkbox 状态选择输入后端
        if hasattr(self, 'chk_bg_mode'):
            if self.chk_bg_mode.isChecked():
                from ...core.desktop import PostMessageInput
                self._input = PostMessageInput(
                    input_sim=self._user_config.input_sim,
                    hwnd=w["hwnd"],
                )
            else:
                from ...core.desktop import SendInputInput
                self._input = SendInputInput(input_sim=self._user_config.input_sim)
                self.log_text.append(tr("[模式] 已切换到前台模式（SendInput，移动光标）"))

    def _on_red_box_changed(self, state):
        """红框标定开关：仅控制定位窗口后边缘是否显示红色标记框。

        不读写 _user_config，纯运行期状态——取消勾选立刻隐藏当前边框，
        重新勾选且仍处于定位状态则立刻按当前窗口位置重新画出。
        """
        if bool(state):
            if self._target_window is not None:
                w = self._target_window
                self._overlay.show_border(w['left'], w['top'], w['width'], w['height'])
                self._overlay.set_color("red")
        else:
            self._overlay.hide_border()

    def _on_bg_mode_changed(self, state):
        """后台模式开关切换：在 PostMessageInput / SendInputInput 之间替换整个 _input 实例"""
        if not self._target_window:
            return
        hwnd = self._target_window["hwnd"]
        if bool(state):
            from ...core.desktop import PostMessageInput
            self._input = PostMessageInput(
                input_sim=self._user_config.input_sim,
                hwnd=hwnd,
            )
            self.log_text.append(tr("[模式] 已切换到后台模式（PostMessage，不移动光标）"))
        else:
            from ...core.desktop import SendInputInput
            self._input = SendInputInput(input_sim=self._user_config.input_sim)
            self.log_text.append(tr("[模式] 已切换到前台模式（SendInput，移动光标）"))

    def _on_capture_method_changed(self, state):
        """截图方式开关切换：在 screencap / scrcpy 之间重建截图后端"""
        method = "scrcpy" if state else "screencap"

        # 录屏依赖流式推帧，切换前自动转正保存
        if self._screen_recorder is not None:
            self._abort_screen_record(tr("切换截图方式"))

        if not self._device:
            # 未连接设备时仅更新内存配置
            self._user_config.android_capture_method = method
            return

        # 已连接设备时只在 screencap / scrcpy 之间重建截图后端。
        self._user_config.android_capture_method = method
        from ...core.android import create_capture_backend
        old_capture = self._capture
        self._capture = create_capture_backend(device=self._device, method=method)
        if self._capture.start():
            # scrcpy 模式订阅帧回调
            self._scrcpy_streaming = False
            if method == "scrcpy":
                from ...core.android import AndroidStreamCapture
                if isinstance(self._capture, AndroidStreamCapture):
                    self._capture.set_on_frame(self._on_scrcpy_frame)
                    self._scrcpy_streaming = True
            # 清理旧后端
            if old_capture:
                try:
                    old_capture.stop()
                except Exception:
                    pass
            mode_label = tr("scrcpy 流式") if method == "scrcpy" else "ADB screencap"
            self.log_text.append(f"[模式] 已切换到 {mode_label} 截图")
            if not self._scrcpy_streaming:
                self._capture_preview()
        else:
            self.log_text.append(f"[错误] {method} 截图后端不可用，回退到 screencap")
            self._user_config.android_capture_method = "screencap"
            if hasattr(self, "chk_scrcpy"):
                self.chk_scrcpy.blockSignals(True)
                self.chk_scrcpy.setChecked(False)
                self.chk_scrcpy.blockSignals(False)
            # 回退到 screencap
            self._capture = create_capture_backend(device=self._device, method="screencap")
            self._capture.start()
        # 流式状态变化后刷新采集面板可用性
        if hasattr(self, "_apply_rec_state"):
            self._apply_rec_state()

    def _on_agent_mode_changed(self, state):
        """设备端手势开关：只改内存配置，下次连接生效（已连接时开关被锁定）"""
        self._user_config.android_input_method = "device_gesture" if state else "adb"
        label = tr("设备端手势（Beta，需安装律匠 App）") if state else "ADB shell input"
        self.log_text.append(f"[模式] 安卓输入方式: {label}（下次连接生效）")

    # ─── 截屏 ─────────────────────────────────────────────

    def _capture_preview(self):
        """截取已定位窗口/设备的截图并展示在预览区。"""
        img = self._grab_capture_image()
        if img is None:
            if self.preview_label.isVisible():
                self.preview_label.setText(tr("截屏失败"))
            return
        self._last_capture = img
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
        from ...core.desktop import DesktopCapture
        if self._capture is None:
            self._capture = DesktopCapture()
        w = self._target_window
        self._capture.set_capture_region(w['left'], w['top'], w['width'], w['height'])
        return self._capture.capture()

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
            from ...core.desktop import DesktopCapture
            if self._capture is None:
                self._capture = DesktopCapture()
            w = self._target_window
            self._capture.set_capture_region(
                w['left'], w['top'], w['width'], w['height']
            )
            img = self._capture.capture()
            if img is not None:
                self._last_capture = img
                return img, None
            return None, tr("截图失败")
        except Exception as e:
            logger.error(f"刷新截图失败: {e}")
            return None, f"截图失败: {e}"

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

    def _on_scrcpy_frame(self, bgr: np.ndarray):
        """scrcpy 解码线程回调：通过 Qt 信号将帧转发到 UI 线程，并分叉喂给录屏器"""
        if hasattr(self, "_scrcpy_frame_ready"):
            self._scrcpy_frame_ready.emit(bgr)
        # 录屏分叉：push 仅入队不阻塞解码线程，暂停/停止态内部直接丢弃
        rec = self._screen_recorder
        if rec is not None:
            rec.push(bgr)

    def _on_scrcpy_frame_ui(self, bgr: np.ndarray):
        """UI 线程槽：更新预览区显示（由 _scrcpy_frame_ready 信号触发）

        预览隐藏时仅更新 _last_capture，跳过 BGR→RGB→QPixmap 转换以节省 CPU。
        """
        if not self._device_ready:
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
