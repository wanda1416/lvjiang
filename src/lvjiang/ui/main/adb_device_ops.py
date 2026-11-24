"""主窗口的 ADB / 无线调试设备扫描、连接与后台工作线程。"""


import weakref

from loguru import logger
from PyQt6 import sip
from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal

from ...core.config import load_user_config
from ...i18n import tr
from ..mobile.device_scan import DeviceScanPanel


def _pending_uncertain(device) -> bool:
    """该设备上此刻是否有人阻塞在「已发出、结果不确定」的确认上。

    只有这种等待必须由用户亲自点「继续」；重连只是把通道修好，不能代替
    这次确认，否则一次可能已经执行的点击会被再放一次。判定以设备自己记的
    「正在等待」为准（`AndroidRecovery.waiting`），不看 `recovering`——后者
    只说明传输在重建，透明重连也会置位。
    """
    coordinator = getattr(device, "recovery_coordinator", None)
    if not getattr(coordinator, "waiting", False):
        return False
    return getattr(coordinator, "waiting_kind", "") == "uncertain_action"


class _AdbConnSignalBridge(QObject):
    """工作流线程 → 主线程的 ADB 断连 / 画面流故障信号桥"""
    adb_lost = pyqtSignal(str, str, object)
    adb_restored = pyqtSignal(str)
    stream_lost = pyqtSignal(str, str, str)   # target_id, 归因, 说明

    def __init__(self, target_id: str):
        super().__init__()
        self._target_id = target_id

    def notify_lost(self, error_msg: str, device=None):
        """`device` 是发起通知的那台设备；重连换过设备后两者不是同一个。"""
        self.adb_lost.emit(self._target_id, error_msg, device)

    def notify_restored(self):
        self.adb_restored.emit(self._target_id)

    def notify_stream_lost(self, kind: str, detail: str):
        """画面流故障：来自解码线程或看门狗线程，必须经信号转回主线程。"""
        self.stream_lost.emit(self._target_id, kind, detail)


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
                 agent_mode: bool = False, subnets: list | None = None,
                 allow_adb_fallback: bool = True, auto_start_shizuku: bool = False):
        super().__init__()
        self._task = task
        self._serial = serial
        self._capture_method = capture_method
        self._agent_mode = agent_mode
        self._allow_adb_fallback = allow_adb_fallback
        self._auto_start_shizuku = auto_start_shizuku
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

        # 端侧执行连接前，按高级设置自动拉起 Shizuku 服务（未运行才启动一次，
        # 只读检测 + 幂等启动，不打断正在执行的任务；失败不阻断连接）。
        if self._agent_mode and self._auto_start_shizuku:
            from ...core.android.shizuku import ensure_shizuku_running
            _ok, msg = ensure_shizuku_running(device)
            self.notice.emit(f"[Shizuku] {msg}")

        # 设备端代理只负责输入手势；截图严格服从用户选择的 screencap / scrcpy。
        # 这样启用 Beta 输入通道不会暗中改变截图命令。
        agent = None
        method = self._capture_method
        if self._agent_mode:
            agent = connect_agent(device)
            if agent is None:
                if not self._allow_adb_fallback:
                    self.error.emit(tr("设备端代理不可用，且高级设置已禁用 ADB 输入降级；请恢复手机代理后重新连接"))
                    return
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




class _AdbDeviceOpsMixin:
    # ─── ADB 设备扫描/连接 ─────────────────────────────────

    def _target_device_refs(self, target_id: str) -> list[weakref.ref]:
        """返回指定目标仍在存活的 AdbDevice 弱引用列表，并顺手清理已回收项。"""
        by_target = getattr(self, "_adb_target_device_refs", None)
        if by_target is None:
            by_target = {}
            self._adb_target_device_refs = by_target
        refs = [ref for ref in by_target.get(target_id, ()) if ref() is not None]
        if refs:
            by_target[target_id] = refs
        else:
            by_target.pop(target_id, None)
        return refs

    def _remember_target_device(self, target_id: str, device) -> None:
        """登记归属该目标的 AdbDevice，供跨多次重连统一改指新传输与恢复回调。"""
        if device is None:
            return
        refs = self._target_device_refs(target_id)
        if any(ref() is device for ref in refs):
            return
        try:
            ref = weakref.ref(device)
        except TypeError:
            return
        by_target = getattr(self, "_adb_target_device_refs", None)
        if by_target is None:
            by_target = {}
            self._adb_target_device_refs = by_target
        refs.append(ref)
        by_target[target_id] = refs

    def _live_target_devices(self, target_id: str, extra=None) -> list:
        """收集该目标下所有仍存活的 AdbDevice（含 extra 且去重保序）。"""
        devices = [dev for ref in self._target_device_refs(target_id) if (dev := ref()) is not None]
        if extra is not None and not any(dev is extra for dev in devices):
            devices.append(extra)
        return devices

    def _dispatch_connection_signal(
        self, target_id: str, method: str, *args,
    ) -> None:
        """把恢复通知动态转发到当前活跃目标的信号桥，忽略已释放目标的竞态通知。"""
        target = self._execution_targets.get(target_id)
        bridge = getattr(target, "connection_bridge", None)
        if bridge is None:
            return
        if isinstance(bridge, QObject) and sip.isdeleted(bridge):
            return
        try:
            getattr(bridge, method)(*args)
        except RuntimeError:
            pass

    def _notify_adb_connection_lost(
        self, target_id: str, message: str, device=None,
    ) -> None:
        self._dispatch_connection_signal(target_id, "notify_lost", message, device)

    def _notify_adb_connection_restored(self, target_id: str) -> None:
        self._dispatch_connection_signal(target_id, "notify_restored")

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
            self.append_log(tr("[提示] 未发现 USB 连接的 ADB 设备"))
            self.statusBar().showMessage(tr("未发现设备 | 可尝试局域网扫描"))
            # 弹出对话框询问是否扫描局域网
            self._ask_wireless_scan()
            return

        for d in devices:
            label = d["serial"] + (f"  ({d['model']})" if d["model"] else "")
            self.window_combo.addItem(label, d)
        self._set_locate_enabled(True)
        self.append_log(f"[扫描] 找到 {len(devices)} 台设备，请选择并点击连接")
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
            self.append_log(tr("[扫描] 正在探测本机模拟器 ADB 端口..."))
        else:
            self.btn_scan_device.setText(tr("扫描局域网..."))
            self.statusBar().showMessage(tr("正在扫描局域网..."))
            scope = "全部网卡" if not subnets else "、".join(f"{s}0/24" for s in subnets)
            self.append_log(f"[扫描] 正在扫描局域网 ADB 设备（{scope}）...")

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
                self.append_log(tr("[扫描] 本机未发现开放的模拟器 ADB 端口"))
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
                self.append_log(tr("[扫描] 局域网内未发现可连接的 ADB 设备"))
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
        self.append_log(f"[扫描] 发现 {len(devices)} 台设备，请选择并点击连接")
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
        self.append_log(f"[错误] 局域网扫描失败: {error_msg}")
        self.statusBar().showMessage(tr("扫描失败 | 详见日志"))

    def _on_scan_devices_error(self, error_msg: str):
        """扫描失败回调（主线程）"""
        self.btn_scan_window.setEnabled(True)
        self.btn_scan_device.setEnabled(True)
        self.btn_scan_device.setText(tr("扫描设备"))
        logger.error(f"扫描设备失败: {error_msg}")
        self.append_log(f"[错误] 扫描设备失败: {error_msg}")
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
            allow_adb_fallback=load_user_config().android_recovery.allow_adb_fallback,
            auto_start_shizuku=load_user_config().android_recovery.auto_start_shizuku,
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
        # 本机模拟器多开的实例共享稳定身份，需把 transport 地址并入 ID 才能并存；
        # 真机仍只按身份归并（USB 与无线是一个目标）。
        target_id = android_target_id(
            identity.value, str(getattr(device, "serial", "") or ""))
        conflict = self._unconfirmed_identity_conflict(target_id, identity.stable)
        if conflict is not None:
            # 读不到任何稳定标识时，无法判断这条 transport 是不是已连目标的
            # 另一条通道。放行就可能让同一台设备变成两个可执行目标，两个任务
            # 各自以为独占它；自动归并又可能把两台设备错当成一台。两者都不能
            # 猜，所以拦在前台应用探测、历史登记这些副作用之前。
            self._release_rejected_connection(capture, agent)
            self.append_log(tr(
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
        # 先把所有仍存活的旧 AdbDevice 更新到新连接，再唤醒正在
        # _handle_connection_error 中等待的失败命令，这样多次重连后的在飞旧设备
        # 也不会继续打旧 IP，且回调始终动态路由到当前最新 target.connection_bridge。
        old_device = existing_target.device if existing_target is not None else None
        tracked_devices = self._live_target_devices(target_id, extra=old_device)
        for dev in tracked_devices:
            dev.serial = device.serial
            dev.adb_path = device.adb_path
        device.resume_event = resume_event
        try:
            from ...core.app_controller import record_connected_android
            app_info = record_connected_android(
                device, width=w, height=h, target_id=target_id)
            if app_info.get("package"):
                self.append_log(
                    f"[应用识别] {app_info['package']}/{app_info['activity']}")
        except Exception as exc:  # noqa: BLE001 - 连接不应因前台应用探测失败而失败
            logger.warning(f"ADB 当前应用信息获取失败: {exc}")

        bridge = _AdbConnSignalBridge(target_id)
        bridge.adb_lost.connect(self._on_adb_connection_lost)
        bridge.adb_restored.connect(self._on_adb_connection_restored)
        bridge.stream_lost.connect(self._on_stream_lost)

        def wire(dev) -> None:
            """同一个目标的所有 AdbDevice 共用一套动态路由恢复回调。

            不直接闭包绑定短命 ``bridge``，而是按 ``target_id`` 动态读取当下活跃
            target 的 ``connection_bridge``，避免连续多次重连释放旧 bridge 后
            更早在飞命令打到已销毁的 Qt 对象；同时带上发起通知的 ``dev``，
            让宿主区分断线归属。
            """
            dev.on_connection_lost = (
                lambda message, tid=target_id, dev=dev:
                self._notify_adb_connection_lost(tid, message, dev))
            dev.on_connection_restored = (
                lambda tid=target_id: self._notify_adb_connection_restored(tid))
            dev.can_wait_for_recovery = lambda tid=target_id: self._run_manager.run_for_target(tid) is not None
            dev.stop_check = lambda tid=target_id: bool(
                (run := self._run_manager.run_for_target(tid))
                and run.stop_event.is_set())

        wire(device)
        self._remember_target_device(target_id, device)
        for dev in tracked_devices:
            wire(dev)
            self._remember_target_device(target_id, dev)

        # 画面流健康：故障经信号桥回主线程，恢复走统一的 recover_stream 阶梯
        self._install_stream_hooks(
            capture, target_id, generation, bridge, device)

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
        # 「已发出、结果不确定」的动作必须由用户点「继续」确认后才重放：重连
        # 只修好通道，不能替这次确认，也不提前为它宣告已恢复（该等待的展示由
        # _on_adb_connection_restored 在一次请求真正成功后才更新）。等通道本身
        # 的等待不受影响，仍在重连后立即唤醒并复位运行状态。
        # 确认权以存在运行实例为前提：没有运行实例就没有等待者，放行无副作用。
        holds_confirmation = (
            any(_pending_uncertain(dev) for dev in tracked_devices)
            and self._run_manager.run_for_target(target_id) is not None)
        old = self._execution_targets.put(target)
        if old is not None:
            self._dispose_execution_target(old)
        if not holds_confirmation:
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
        self._activate_connected_target(target, old)
        # 连接成功就该收起上一个会话留下的通道横幅。原先只有「运行实例正处于
        # waiting_target」才收，空闲重连时横幅会一直挂着「画面流已中断」——用户
        # 明明已经连上了，却只能重启应用把它消掉。通道好了不等于用户确认了那个
        # 「已发出、结果不确定」的动作，那种情况下横幅保留、只换说法。
        if holds_confirmation:
            self._show_channel_banner(target_id, tr(
                "⚠ 执行通道等待确认：处理问题后点击「重新检查」；"
                "无法恢复时请按 F10 停止任务"))
        else:
            self._hide_channel_banner(target_id)

        method_label = tr("流式截图") if capture_method == "scrcpy" \
            else tr("单帧截图")
        method_label = (
            (tr("端侧执行") if agent is not None else tr("指令执行"))
            + "  |  " + method_label)
        self.append_log(f"[连接成功] {combo_data['serial']} ({w}x{h}) [{method_label}]")
        if not identity.stable:
            logger.warning(
                f"[连接] {combo_data['serial']} 读不到稳定设备标识，"
                f"目标身份退化为连接地址")
            self.append_log(tr(
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
        if (self._execution_targets.active_target_id == target_id
                and not streaming
                and getattr(self, "_preview_enabled", False)):
            self._capture_preview()

    def _on_connect_error(
            self, error_msg: str, *, update_candidate_ui: bool = True):
        """连接失败回调（主线程）"""
        logger.error(f"连接设备失败: {error_msg}")
        self.append_log(f"[错误] 连接设备失败: {error_msg}")
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
            self.append_log(tr("[提示] 该目标正在执行任务，不能断开"))
            return
        was_active = target.id == self._execution_targets.active_target_id
        # 主动断开一个空闲目标后，它的通道横幅必须跟着消失：目标都没了，
        # 「画面流已中断，点重新检查或断开重连设备」就成了一条永远点不掉的提示。
        # 有运行实例的目标在上面已经被拦住，能走到这里的一定是空闲目标。
        hide_banner = getattr(self, "_hide_channel_banner", None)
        if callable(hide_banner):
            hide_banner(target.id)
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
        self.append_log(f"[断连] {target.display_name}")

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
            self.append_log(
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
            if getattr(self, "_preview_enabled", False):
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
        self.append_log(
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
            self.append_log(tr("[提示] 该目标正在执行任务，不能切换输入方式"))
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
        self.append_log(
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
            self.append_log(tr("[提示] 该目标正在执行任务，不能切换截图方式"))
            return
        if enabled and not bool(
                getattr(target.input_ctrl, "background_mode", False)):
            self._set_window_target_background_input(target_id, True)
        from ...core.desktop import DesktopCapture, WgcCapture, wgc_available
        if enabled:
            available, reason = wgc_available()
            if not available:
                self.append_log(tr("[截图] 后台截图不可用：") + reason)
                return
        capture = WgcCapture() if enabled else DesktopCapture()
        window = target.window
        capture.set_capture_region(
            window["left"], window["top"], window["width"], window["height"])
        if isinstance(capture, WgcCapture) \
                and not capture.attach_hwnd(window["hwnd"]):
            capture.stop()
            self.append_log(tr("[截图] 后台截图启动失败，保留原截图方式"))
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
            if getattr(self, "_preview_enabled", False):
                self._capture_preview()
        self._refresh_execution_targets_ui()

    def _set_android_target_streaming(
            self, target_id: str, enabled: bool) -> None:
        """只修改指定设备目标的实际截图后端。"""
        target = self._execution_targets.get(target_id)
        if target is None or target.kind != "adb" or target.device is None:
            return
        if self._target_has_active_run(target.id):
            self.append_log(tr("[提示] 该目标正在执行任务，不能切换截图方式"))
            return
        method = "scrcpy" if enabled else "screencap"
        from ...core.android import AndroidStreamCapture, create_capture_backend
        capture = create_capture_backend(device=target.device, method=method)
        if not capture.start():
            self.append_log(
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
        if streaming:
            self._install_stream_hooks(
                capture, target_id, generation, target.connection_bridge, target.device)
        self._rebind_target_resources(target)
        if old_capture is not None:
            try:
                old_capture.stop()
            except Exception as exc:
                logger.debug(f"停止旧截图后端时报错（忽略）: {exc}")
        if self._execution_targets.active_target_id == target_id:
            self._sync_active_target_compat()
            if not streaming and getattr(self, "_preview_enabled", False):
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

