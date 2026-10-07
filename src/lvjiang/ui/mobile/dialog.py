"""「移动设备」工具：装包、设备扫描、设备体检、手势实测。

安卓侧的能力早就齐了——APK 随 Release 发布、输入时间线能在设备端编译成并发手势、
设备端还一直在上报协议与权限状态——但软件里没有任何一处告诉用户这些东西存在：
APK 只出现在 GitHub 的 Release 页，手势探针只有命令行，status 的十几个字段一个字
都没显示过。这个对话框就是补这个入口。
"""
from __future__ import annotations

from html import escape
from pathlib import Path

from loguru import logger
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from lvjiang.ui.combo_box import AutoWidthComboBox

from ...core.android.agent import PROTOCOL_VERSION, connect_agent_diagnostic
from ...core.android.apk_release import (
    ApkDownloadError,
    ApkFile,
    apk_asset_name,
    apk_download_url,
    default_download_dir,
    download_apk,
    fetch_expected_sha256,
    file_sha256,
    find_local_apk,
    inspect_local_apk,
)
from ...core.android.apk_server import ApkLanServer, lan_addresses
from ...core.android.device import AdbDevice, list_adb_devices
from ...core.android.gesture_probe import (
    measure_round_trip,
    run_concurrent_probe,
    screen_size,
)
from ...core.update import get_version
from ...i18n import tr
from ..button_styles import apply_button_style
from .device_scan import DeviceDiscoveryWorker, DeviceScanPanel
from .diagnostics import build_checks, build_report, capability_notes
from .qr_widget import QrCodeWidget

_LEVEL_COLOR = {"ok": "#2e7d32", "warn": "#e65100", "bad": "#c62828"}


class _DownloadWorker(QThread):
    """后台下载 APK。下载几十兆不能卡住界面。"""

    progress = pyqtSignal(int, int)
    done = pyqtSignal(object, str)  # ApkFile | None, 错误消息

    def __init__(self, version: str, dest_dir: Path, parent=None):
        super().__init__(parent)
        self._version = version
        self._dest_dir = dest_dir
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        self.requestInterruption()

    def run(self) -> None:
        try:
            apk = download_apk(
                self._version, self._dest_dir,
                progress=lambda done, total: self.progress.emit(done, total),
                cancelled=lambda: self._cancelled)
        except ApkDownloadError as exc:
            self.done.emit(None, str(exc))
        except Exception as exc:  # noqa: BLE001 — 任何失败都要回到界面上
            logger.exception("下载 APK 失败")
            self.done.emit(None, f"{type(exc).__name__}: {exc}")
        else:
            self.done.emit(apk, "")


class _HashWorker(QThread):
    """给已经在本地的 APK 补算哈希并（能联网时）比对。

    八十兆文件算一遍 sha256 要几百毫秒到一秒，取 SHA256SUMS 还要联网——都不能
    放在界面线程里，否则一打开对话框就卡住。
    """

    done = pyqtSignal(str, str, object)  # path, sha256, verified: bool | None

    def __init__(self, path: Path, version: str, parent=None):
        super().__init__(parent)
        self._path = path
        self._version = version

    def run(self) -> None:
        try:
            digest = file_sha256(self._path)
        except OSError as exc:
            logger.warning(f"计算 APK 哈希失败: {exc}")
            self.done.emit(str(self._path), "", None)
            return
        if self.isInterruptionRequested():
            self.done.emit(str(self._path), "", None)
            return
        expected = fetch_expected_sha256(self._version)
        self.done.emit(
            str(self._path), digest,
            None if expected is None else digest == expected)

    def cancel(self) -> None:
        self.requestInterruption()


class _DeviceScanWorker(QThread):
    """后台枚举 ADB 设备，避免打开窗口前卡住主线程。"""

    done = pyqtSignal(object, str)  # list[dict], error

    def run(self) -> None:
        try:
            devices = list_adb_devices(
                cancel_check=self.isInterruptionRequested)
        except BaseException as exc:  # noqa: BLE001 — 线程必须回传完成态
            logger.exception("枚举 ADB 设备失败")
            self.done.emit([], f"{type(exc).__name__}: {exc}")
        else:
            self.done.emit(devices, "")

    def cancel(self) -> None:
        self.requestInterruption()


class _ApkInspectWorker(QThread):
    """后台校验用户选择的 APK。"""

    done = pyqtSignal(object, str)  # ApkFile | None, error

    def __init__(self, path: Path, version: str, parent=None):
        super().__init__(parent)
        self._path = path
        self._version = version

    def run(self) -> None:
        try:
            apk = inspect_local_apk(self._path, self._version)
        except BaseException as exc:  # noqa: BLE001 — 线程必须回传完成态
            logger.exception("校验本地 APK 失败")
            self.done.emit(None, f"{type(exc).__name__}: {exc}")
        else:
            self.done.emit(apk, "")

    def cancel(self) -> None:
        self.requestInterruption()


class _AdbInstallWorker(QThread):
    """后台安装 APK；取消时终止 adb 子进程。"""

    done = pyqtSignal(str, str, str, str)  # serial, path, output, error

    def __init__(self, serial: str, path: Path, parent=None):
        super().__init__(parent)
        self._serial = serial
        self._path = path

    def run(self) -> None:
        try:
            output = AdbDevice(self._serial).install(
                str(self._path),
                cancel_check=self.isInterruptionRequested,
            )
        except BaseException as exc:  # noqa: BLE001 — 线程必须回传完成态
            self.done.emit(
                self._serial, str(self._path), "",
                f"{type(exc).__name__}: {exc}")
        else:
            self.done.emit(self._serial, str(self._path), output, "")

    def cancel(self) -> None:
        self.requestInterruption()


class _AgentWorker(QThread):
    """连设备取状态，可选再跑一次手势探针与往返延迟。

    连接与探针都是阻塞的（探针本身要按住两秒），必须离开 UI 线程。
    """

    # status, serial, 是否探针, 探针结论, 延迟样本, 异常
    done = pyqtSignal(dict, str, bool, str, list, str)

    def __init__(self, serial: str, *, probe: bool = False,
                 hold: float = 2.0, parent=None):
        super().__init__(parent)
        self._serial = serial
        self._probe = probe
        self._hold = hold

    def run(self) -> None:
        agent = None
        status: dict = {}
        message = ""
        samples: list[float] = []
        try:
            device = AdbDevice(self._serial or None)
            agent = connect_agent_diagnostic(device)
            if agent is None:
                self.done.emit(
                    {}, self._serial, self._probe,
                    tr("无法连接设备端代理"), [], "")
                return
            status = dict(agent.status or {})
            if self._probe:
                if status.get("protocol") != PROTOCOL_VERSION:
                    message = tr("协议版本不匹配，无法执行手势测试")
                elif not status.get("a11y"):
                    message = tr("无障碍服务未开启，无法执行手势测试")
                elif self.isInterruptionRequested():
                    message = tr("手势测试已取消")
                else:
                    width, height = screen_size(agent)
                    if not width or not height:
                        message = tr("拿不到屏幕尺寸，无法下发手势")
                    else:
                        outcome = run_concurrent_probe(
                            agent, width, height, self._hold)
                        prefix = "✔ " if outcome.ok else "✘ "
                        message = (
                            f"{prefix}{outcome.message}\n"
                            + tr("落点：按住 {push}，期间点击 {tap}").format(
                                push=outcome.push_point,
                                tap=outcome.tap_point))
                if not self.isInterruptionRequested():
                    samples = measure_round_trip(agent)
            self.done.emit(
                status, self._serial, self._probe, message, samples, "")
        except BaseException as exc:  # noqa: BLE001 — 线程必须恢复 UI 状态
            logger.exception("移动设备体检失败")
            self.done.emit(
                status, self._serial, self._probe, message, samples,
                f"{type(exc).__name__}: {exc}")
        finally:
            if agent is not None:
                agent.close()

    def cancel(self) -> None:
        self.requestInterruption()


class MobileDeviceDialog(QDialog):
    """工具 → 移动设备。"""

    def __init__(self, host=None, parent=None):
        super().__init__(parent)
        self._host = host
        self.setWindowTitle(tr("移动设备"))
        self.setMinimumSize(720, 560)
        self._version = get_version()
        self._apk: ApkFile | None = None
        self._server: ApkLanServer | None = None
        self._download: _DownloadWorker | None = None
        self._agent_worker: _AgentWorker | None = None
        self._hash_worker: _HashWorker | None = None
        self._device_worker: _DeviceScanWorker | None = None
        self._discovery_worker: DeviceDiscoveryWorker | None = None
        self._inspect_worker: _ApkInspectWorker | None = None
        self._install_worker: _AdbInstallWorker | None = None
        self._workers: set[QThread] = set()
        self._close_pending = False
        self._status: dict = {}
        self._status_serial = ""
        self._setup_ui()
        self._refresh_devices()
        self._adopt_existing_apk()
        self._refresh_install_state()

    # ─── 构建 ────────────────────────────────────────────

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addWidget(QLabel(
            tr("当前版本 <b>{v}</b> · 对应安装包 {name}").format(
                v=self._version, name=apk_asset_name(self._version))))
        header.addStretch()
        header.addWidget(QLabel(tr("设备：")))
        self._device_combo = AutoWidthComboBox()
        self._device_combo.setMinimumWidth(220)
        header.addWidget(self._device_combo)
        self._btn_rescan = QPushButton(tr("重新扫描"))
        self._btn_rescan.clicked.connect(self._refresh_devices)
        apply_button_style(self._btn_rescan, variant="neutral")
        header.addWidget(self._btn_rescan)
        layout.addLayout(header)

        self._tabs = QTabWidget()
        from .offline import OfflineControlPage
        self._offline_page = OfflineControlPage(host=self._host, serial=self._current_serial,
                                                track_worker=self._track_worker, parent=self)
        self._tabs.addTab(self._offline_page, tr("离线任务"))
        self._tabs.addTab(self._build_install_tab(), tr("应用安装"))
        self._device_scan_panel = DeviceScanPanel()
        self._device_scan_panel.scan_requested.connect(
            self._start_device_discovery)
        self._device_scan_panel.cancel_requested.connect(
            self._cancel_device_discovery)
        self._tabs.addTab(self._device_scan_panel, tr("设备扫描"))
        self._tabs.addTab(self._build_health_tab(), tr("设备体检"))
        self._tabs.addTab(self._build_gesture_tab(), tr("手势测试"))
        self._device_combo.currentIndexChanged.connect(lambda _: self._offline_page.device_changed())
        layout.addWidget(self._tabs, 1)

        footer = QHBoxLayout()
        footer.addStretch()
        close = QPushButton(tr("关闭"))
        close.clicked.connect(self.close)
        apply_button_style(close, variant="neutral")
        footer.addWidget(close)
        layout.addLayout(footer)

    def _build_install_tab(self) -> QWidget:
        """左侧只放一个二维码，右侧上面操作、下面状态。

        两个并列的二维码区是没想清楚流程的产物：同一时刻只有一个码该被扫。
        现在由「来源」这对单选决定左侧展示哪一个，二维码只有一处。
        """
        page = QWidget()
        layout = QHBoxLayout(page)

        scan_box = QGroupBox(tr("扫码安装"))
        scan_layout = QVBoxLayout(scan_box)
        self._scan_hint = QLabel()
        self._scan_hint.setWordWrap(True)
        scan_layout.addWidget(self._scan_hint)
        self._qr = QrCodeWidget()
        scan_layout.addWidget(self._qr, 1)
        self._qr_url = QLineEdit()
        self._qr_url.setReadOnly(True)
        scan_layout.addWidget(self._qr_url)
        layout.addWidget(scan_box, 1)

        right = QVBoxLayout()

        source_box = QGroupBox(tr("二维码来源"))
        source_layout = QVBoxLayout(source_box)
        self._source_online = QRadioButton(tr("在线下载（GitHub）"))
        self._source_online.setChecked(True)
        self._source_lan = QRadioButton(tr("本机共享（局域网）"))
        self._source_group = QButtonGroup(self)
        self._source_group.addButton(self._source_online)
        self._source_group.addButton(self._source_lan)
        self._source_online.toggled.connect(self._on_source_changed)
        source_layout.addWidget(self._source_online)
        source_layout.addWidget(self._source_lan)
        right.addWidget(source_box)

        action_box = QGroupBox(tr("操作"))
        action_layout = QVBoxLayout(action_box)
        self._btn_download = QPushButton(tr("下载 APK 到本机"))
        self._btn_download.clicked.connect(self._on_download)
        apply_button_style(self._btn_download)
        action_layout.addWidget(self._btn_download)
        self._btn_pick = QPushButton(tr("选择本地 APK…"))
        self._btn_pick.clicked.connect(self._on_pick_local)
        apply_button_style(self._btn_pick, variant="neutral")
        action_layout.addWidget(self._btn_pick)
        self._btn_adb_install = QPushButton(tr("ADB 直传到设备"))
        self._btn_adb_install.clicked.connect(self._on_adb_install)
        apply_button_style(self._btn_adb_install, variant="neutral")
        action_layout.addWidget(self._btn_adb_install)
        right.addWidget(action_box)

        state_box = QGroupBox(tr("状态"))
        state_layout = QVBoxLayout(state_box)
        self._lan_form = QFormLayout()
        self._lan_combo = AutoWidthComboBox()
        self._lan_combo.setToolTip(tr(
            "手机要访问的本机地址。多网卡时选与手机同网段的那个；"
            "换地址只改链接，不会中断共享"))
        self._lan_combo.currentIndexChanged.connect(
            lambda _i: self._refresh_qr())
        self._lan_form.addRow(tr("本机地址："), self._lan_combo)
        state_layout.addLayout(self._lan_form)
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        state_layout.addWidget(self._progress)
        self._apk_info = QLabel(tr("本机尚无安装包"))
        self._apk_info.setWordWrap(True)
        self._apk_info.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        state_layout.addWidget(self._apk_info)
        state_layout.addStretch()
        right.addWidget(state_box, 1)

        layout.addLayout(right, 1)
        return page

    def _build_health_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self._btn_check = QPushButton(tr("开始体检"))
        self._btn_check.clicked.connect(lambda: self._run_agent(probe=False))
        apply_button_style(self._btn_check)
        row.addWidget(self._btn_check)
        self._btn_report = QPushButton(tr("复制诊断报告"))
        self._btn_report.clicked.connect(self._on_copy_report)
        apply_button_style(self._btn_report, variant="neutral")
        row.addWidget(self._btn_report)
        row.addStretch()
        layout.addLayout(row)
        self._health_text = QTextEdit()
        self._health_text.setReadOnly(True)
        layout.addWidget(self._health_text, 1)
        return page

    def _build_gesture_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel(tr(
            "测试会在屏幕左下角按住一段时间，期间在右下角点两次——"
            "也就是「推着摇杆同时连点」。\n"
            "请先把手机停在游戏里能看出移动和跳跃的画面，再点开始。")))
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("按住时长（秒）：")))
        self._hold_spin = QDoubleSpinBox()
        self._hold_spin.setRange(0.5, 10.0)
        self._hold_spin.setSingleStep(0.5)
        self._hold_spin.setValue(2.0)
        row.addWidget(self._hold_spin)
        self._btn_probe = QPushButton(tr("开始测试"))
        self._btn_probe.clicked.connect(lambda: self._run_agent(probe=True))
        apply_button_style(self._btn_probe)
        row.addWidget(self._btn_probe)
        row.addStretch()
        layout.addLayout(row)
        self._gesture_text = QTextEdit()
        self._gesture_text.setReadOnly(True)
        layout.addWidget(self._gesture_text, 1)
        return page

    # ─── 设备 ────────────────────────────────────────────

    def _current_serial(self) -> str:
        return str(self._device_combo.currentData() or "")

    def _refresh_devices(self) -> None:
        if self._discovery_worker is not None \
                and self._discovery_worker.isRunning():
            return
        if self._device_worker is not None and self._device_worker.isRunning():
            return
        previous = self._current_serial()
        self._device_combo.clear()
        self._device_combo.addItem(tr("正在扫描…"), "")
        self._device_combo.setEnabled(False)
        self._btn_rescan.setEnabled(False)
        worker = _DeviceScanWorker(self)
        worker.done.connect(
            lambda devices, error: self._on_devices_done(
                devices, error, previous))
        self._device_worker = worker
        self._track_worker(worker)
        worker.start()

    def _on_devices_done(
        self, devices: object, error: str, previous: str,
    ) -> None:
        self._device_worker = None
        self._apply_device_list(devices, error, previous)

    def _apply_device_list(
        self, devices: object, error: str, previous: str,
    ) -> None:
        """更新全局设备选择；扫描 Tab 与普通枚举共用同一出口。"""
        self._device_combo.clear()
        device_list = devices if isinstance(devices, list) else []
        for item in device_list:
            serial = str(item.get("serial") or "")
            model = str(item.get("model") or "")
            self._device_combo.addItem(
                f"{model} ({serial})" if model else serial, serial)
        if not device_list:
            self._device_combo.addItem(
                tr("扫描失败") if error else tr("未发现设备"), "")
            if error:
                self._device_combo.setToolTip(error)
        else:
            restored = self._device_combo.findData(previous)
            if restored >= 0:
                self._device_combo.setCurrentIndex(restored)
            self._device_combo.setToolTip("")
        self._device_combo.setEnabled(True)
        self._btn_rescan.setEnabled(True)
        self._refresh_install_state()

    def _start_device_discovery(
        self, mode: str, subnets: object,
    ) -> None:
        """扫描新的 ADB transport，但不改变主窗口的执行目标。"""
        if self._discovery_worker is not None \
                and self._discovery_worker.isRunning():
            return
        if self._device_worker is not None and self._device_worker.isRunning():
            self._device_scan_panel.finish(
                [], error=tr("正在刷新设备列表，请稍后重试"))
            return
        previous = self._current_serial()
        self._btn_rescan.setEnabled(False)
        selected = subnets if isinstance(subnets, list) else None
        worker = DeviceDiscoveryWorker(mode, selected, self)
        worker.progress.connect(self._device_scan_panel.update_progress)
        worker.done.connect(
            lambda devices, error, cancelled: self._on_device_discovery_done(
                devices, error, cancelled, previous))
        self._discovery_worker = worker
        self._track_worker(worker)
        worker.start()

    def _cancel_device_discovery(self) -> None:
        worker = self._discovery_worker
        if worker is not None and worker.isRunning():
            worker.cancel()

    def _on_device_discovery_done(
        self,
        devices: object,
        error: str,
        cancelled: bool,
        previous: str,
    ) -> None:
        self._discovery_worker = None
        device_list = devices if isinstance(devices, list) else []
        self._device_scan_panel.finish(
            device_list, error=error, cancelled=cancelled)
        if not cancelled:
            self._apply_device_list(device_list, error, previous)
        else:
            self._btn_rescan.setEnabled(True)

    # ─── 安装页 ──────────────────────────────────────────

    def _refresh_install_state(self) -> None:
        """统一算一遍：哪些操作可用、地址列表、以及该展示哪个二维码。

        只有这一处决定界面状态，所以下载完、装完、换地址、停止共享都走同一条
        路——以前链接和二维码分散在几个分支里设，总有分支忘了刷。
        """
        has_apk = self._apk is not None
        addresses = lan_addresses()

        # 保留用户已选的地址：这个方法在装包、重新扫描之后都会跑，
        # 每次重置回第一个网卡会把用户刚选对的那个冲掉。
        previous = str(self._lan_combo.currentData() or "")
        self._lan_combo.blockSignals(True)
        self._lan_combo.clear()
        for address in addresses:
            self._lan_combo.addItem(address, address)
        if not addresses:
            self._lan_combo.addItem(tr("无可用局域网地址"), "")
        restored = self._lan_combo.findData(previous) if previous else -1
        if restored >= 0:
            self._lan_combo.setCurrentIndex(restored)
        self._lan_combo.blockSignals(False)

        # 本机共享要同时有包和地址；缺哪个就说哪个，别让人对着灰按钮猜
        lan_ready = has_apk and bool(addresses)
        self._source_lan.setEnabled(lan_ready)
        if lan_ready:
            self._source_lan.setToolTip(tr("由本机提供下载，手机不需要能上网"))
        elif not has_apk:
            self._source_lan.setToolTip(tr("先「下载 APK 到本机」或选择一个本地安装包"))
        else:
            self._source_lan.setToolTip(tr("没有可用的局域网地址：确认本机已连接网络"))

        serial = self._current_serial()
        self._btn_adb_install.setEnabled(has_apk and bool(serial))
        self._btn_adb_install.setToolTip(
            "" if (has_apk and serial)
            else (tr("先准备一个本地安装包") if not has_apk
                  else tr("没有已连接的 ADB 设备")))

        # 地址只在本机共享时有意义
        self._lan_form.setRowVisible(0, self._source_lan.isChecked())
        self._refresh_qr()

    def _refresh_qr(self) -> None:
        """按当前来源算出唯一那个该被扫的二维码。"""
        if self._source_lan.isChecked() and self._server is not None:
            host = str(self._lan_combo.currentData() or "")
            if not host:
                self._show_qr("", tr("没有可用的局域网地址"))
                return
            self._show_qr(
                self._server.url_for(host),
                tr("用手机扫码，从本机下载安装。手机需要和电脑在同一局域网，"
                   "但不需要能上网。"))
            return
        self._show_qr(
            apk_download_url(self._version),
            tr("用手机扫码，直接从 GitHub 下载安装。\n"
               "手机上不了网时，改用右侧「本机共享」。"))

    def _show_qr(self, url: str, hint: str) -> None:
        self._scan_hint.setText(hint)
        self._qr_url.setText(url)
        if url and not self._qr.set_content(url):
            self._qr_url.setToolTip(tr("二维码生成失败，请手动打开上面的链接"))
        elif not url:
            self._qr.set_content("")

    def _on_source_changed(self, _checked: bool) -> None:
        """切来源即开/停本机共享——选项本身就是那个开关，不另设按钮。"""
        if self._source_lan.isChecked():
            if not self._start_server():
                self._source_online.setChecked(True)
                return
        else:
            self._stop_server()
        self._refresh_install_state()

    def _start_server(self) -> bool:
        if self._server is not None:
            return True
        if self._apk is None or not str(self._lan_combo.currentData() or ""):
            return False
        try:
            self._server = ApkLanServer(self._apk.path).start()
        except OSError as exc:
            QMessageBox.warning(self, tr("无法启动本机共享"), str(exc))
            self._server = None
            return False
        return True

    def _adopt_existing_apk(self) -> None:
        """启动时找回上次下载的包。

        不然用户下载完一次、重启软件后界面又说"本机尚无安装包"，而文件明明还在
        data/apk 里躺着。先按"未校验"立刻显示出来，哈希与校验在后台补——让人
        先看到它存在，比等一秒钟算完更重要。
        """
        path = find_local_apk(self._version)
        if path is None:
            return
        # 走 _set_apk 而不是直接赋值：状态文字、可用操作都挂在它上面，
        # 绕过去就会出现"按钮能点但界面说没有包"。
        self._set_apk(ApkFile(path=path, version=self._version,
                              sha256="", verified=None))
        self._start_hash_worker(path)

    def _start_hash_worker(self, path: Path) -> None:
        if self._hash_worker is not None and self._hash_worker.isRunning():
            return
        worker = _HashWorker(path, self._version, self)
        worker.done.connect(self._on_hash_done)
        self._hash_worker = worker
        self._track_worker(worker)
        worker.start()

    def _on_hash_done(
        self, path: str, digest: str, verified: object,
    ) -> None:
        self._hash_worker = None
        if (self._apk is None or self._apk.path != Path(path) or not digest):
            return
        self._set_apk(ApkFile(
            path=self._apk.path, version=self._apk.version,
            sha256=digest,
            verified=verified if isinstance(verified, bool) else None))

    def _set_apk(self, apk: ApkFile) -> None:
        restart_server = (
            self._server is not None
            and (self._apk is None or self._apk.path != apk.path)
        )
        if restart_server:
            self._stop_server()
        self._apk = apk
        if restart_server and self._source_lan.isChecked():
            if not self._start_server():
                self._source_online.setChecked(True)
        if not apk.sha256:
            detail = tr("正在校验…")
        else:
            verified = {
                True: tr("校验通过"), False: tr("校验不一致"),
                None: tr("未比对（取不到 SHA256SUMS）"),
            }[apk.verified]
            detail = f"sha256 {apk.sha256[:16]}… · {verified}"
        self._apk_info.setText(
            f"{apk.path}\n{apk.size / 1024 / 1024:.1f} MB · {detail}")
        self._refresh_install_state()

    def _on_download(self) -> None:
        if self._inspect_worker is not None and self._inspect_worker.isRunning():
            return
        if self._download is not None and self._download.isRunning():
            self._download.cancel()
            return
        dest = default_download_dir()
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)
        self._btn_download.setText(tr("取消下载"))
        self._btn_pick.setEnabled(False)
        worker = _DownloadWorker(self._version, dest, self)
        worker.progress.connect(self._on_download_progress)
        worker.done.connect(self._on_download_done)
        self._download = worker
        self._track_worker(worker)
        worker.start()

    def _on_download_progress(self, done: int, total: int) -> None:
        if total > 0:
            self._progress.setRange(0, total)
            self._progress.setValue(done)
        self._apk_info.setText(tr("已下载 {mb:.1f} MB").format(
            mb=done / 1024 / 1024))

    def _on_download_done(self, apk: object, error: str) -> None:
        self._progress.setVisible(False)
        self._btn_download.setText(tr("下载 APK 到本机"))
        self._btn_pick.setEnabled(True)
        self._download = None
        if isinstance(apk, ApkFile):
            self._set_apk(apk)
            return
        self._apk_info.setText(error or tr("下载失败"))

    def _on_pick_local(self) -> None:
        if ((self._inspect_worker is not None
             and self._inspect_worker.isRunning())
                or (self._download is not None and self._download.isRunning())):
            return
        path, _filter = QFileDialog.getOpenFileName(
            self, tr("选择 APK"), "", tr("Android 安装包 (*.apk)"))
        if not path:
            return
        self._btn_pick.setEnabled(False)
        self._btn_download.setEnabled(False)
        self._apk_info.setText(tr("正在校验本地 APK…"))
        worker = _ApkInspectWorker(Path(path), self._version, self)
        worker.done.connect(self._on_inspect_done)
        self._inspect_worker = worker
        self._track_worker(worker)
        worker.start()

    def _on_inspect_done(self, apk: object, error: str) -> None:
        self._inspect_worker = None
        self._btn_pick.setEnabled(True)
        self._btn_download.setEnabled(True)
        if isinstance(apk, ApkFile):
            self._set_apk(apk)
            return
        self._apk_info.setText(tr("本地 APK 校验失败：{err}").format(err=error))

    def _on_adb_install(self) -> None:
        if (self._apk is None
                or (self._install_worker is not None
                    and self._install_worker.isRunning())):
            return
        serial = self._current_serial()
        if not serial:
            return
        self._btn_adb_install.setEnabled(False)
        self._apk_info.setText(tr("正在通过 ADB 安装…"))
        worker = _AdbInstallWorker(serial, self._apk.path, self)
        worker.done.connect(self._on_install_done)
        self._install_worker = worker
        self._track_worker(worker)
        worker.start()

    def _on_install_done(
        self, _serial: str, _path: str, output: str, error: str,
    ) -> None:
        self._install_worker = None
        if self._apk is None or self._apk.path != Path(_path):
            self._refresh_install_state()
            return
        if error:
            self._apk_info.setText(tr("安装失败：{err}").format(err=error))
        else:
            self._apk_info.setText(tr("安装完成：{out}").format(out=output))
        self._refresh_install_state()

    def _stop_server(self) -> None:
        if self._server is None:
            return
        try:
            self._server.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"停止 APK 服务失败: {exc}")
        self._server = None

    # ─── 体检 / 手势 ─────────────────────────────────────

    def _run_agent(self, *, probe: bool) -> None:
        if self._agent_worker is not None and self._agent_worker.isRunning():
            return
        target = self._gesture_text if probe else self._health_text
        target.setPlainText(tr("正在连接设备端代理…"))
        self._btn_check.setEnabled(False)
        self._btn_probe.setEnabled(False)
        self._device_combo.setEnabled(False)
        self._btn_rescan.setEnabled(False)
        worker = _AgentWorker(
            self._current_serial(), probe=probe,
            hold=float(self._hold_spin.value()), parent=self)
        worker.done.connect(self._on_agent_done)
        self._agent_worker = worker
        self._track_worker(worker)
        worker.start()

    def _on_agent_done(
        self,
        status: dict,
        serial: str,
        probe: bool,
        message: str,
        samples: list,
        error: str,
    ) -> None:
        self._btn_check.setEnabled(True)
        self._btn_probe.setEnabled(True)
        self._device_combo.setEnabled(True)
        self._btn_rescan.setEnabled(True)
        self._agent_worker = None
        self._status = dict(status)
        self._status_serial = serial
        health = self._render_health(status)
        if error:
            health += (
                f"<hr><div style='color:{_LEVEL_COLOR['bad']}'>"
                f"{escape(tr('体检失败：{err}').format(err=error))}</div>")
        self._health_text.setHtml(health)
        if probe:
            lines = [message] if message else []
            if samples:
                ordered = sorted(samples)
                median = ordered[len(ordered) // 2]
                lines.append(tr(
                    "往返延迟：中位 {mid:.0f} ms（{n} 次，最快 {lo:.0f} / "
                    "最慢 {hi:.0f}）").format(
                        mid=median, n=len(samples),
                        lo=ordered[0], hi=ordered[-1]))
            if error:
                lines.append(tr("手势测试失败：{err}").format(err=error))
            if not lines:
                lines.append(tr("无法连接设备端代理"))
            self._gesture_text.setPlainText("\n".join(lines))

    def _render_health(self, status: dict) -> str:
        rows = []
        for item in build_checks(status, self._version, PROTOCOL_VERSION):
            color = _LEVEL_COLOR.get(item.level, "#444444")
            hint = (f"<div style='color:#666;margin-left:1em'>→ {item.hint}</div>"
                    if item.hint else "")
            rows.append(
                f"<div><b>{item.name}</b>："
                f"<span style='color:{color}'>{item.value}</span></div>{hint}")
        rows.append("<hr>")
        rows.extend(f"<div>{note}</div>" for note in capability_notes(status))
        return "".join(rows)

    def _on_copy_report(self) -> None:
        from PyQt6.QtWidgets import QApplication

        extra = {}
        if self._apk is not None:
            extra[tr("本地安装包")] = f"{self._apk.path.name} ({self._apk.sha256[:16]}…)"
        report = build_report(
            self._status, self._version, PROTOCOL_VERSION,
            self._status_serial or self._current_serial(), extra)
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(report)
        self._health_text.setPlainText(report)

    # ─── 生命周期 ────────────────────────────────────────

    def _track_worker(self, worker: QThread) -> None:
        """登记后台线程；关闭窗口前必须等登记集合真正清空。"""
        self._workers.add(worker)
        worker.finished.connect(lambda: self._on_worker_finished(worker))

    def _on_worker_finished(self, worker: QThread) -> None:
        self._workers.discard(worker)
        if self._close_pending and not any(
                item.isRunning() for item in self._workers):
            QTimer.singleShot(0, self.close)

    def _cancel_workers(self) -> None:
        for worker in tuple(self._workers):
            if not worker.isRunning():
                continue
            cancel = getattr(worker, "cancel", None)
            if callable(cancel):
                cancel()
            else:
                worker.requestInterruption()

    def closeEvent(self, event) -> None:  # noqa: N802
        """对话框关掉就收回临时开放的端口，不留后台服务。"""
        running = [worker for worker in self._workers if worker.isRunning()]
        if running:
            self._close_pending = True
            self._cancel_workers()
            self._stop_server()
            self.setEnabled(False)
            self.setWindowTitle(tr("移动设备（正在停止后台操作…）"))
            event.ignore()
            return
        self._stop_server()
        super().closeEvent(event)
