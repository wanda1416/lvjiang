"""可嵌入的 ADB 设备扫描面板与后台任务。"""

from __future__ import annotations

from loguru import logger
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from lvjiang.ui.combo_box import AutoWidthComboBox

from ...core.android import (
    list_adb_devices,
    list_ipv4_interfaces,
    scan_and_connect_local,
    scan_and_connect_wireless,
)
from ...i18n import tr
from ..button_styles import apply_button_style, fit_button_width


class DeviceDiscoveryWorker(QThread):
    """执行局域网或本机端口扫描，结束后返回完整 ADB 设备列表。"""

    progress = pyqtSignal(str, int, int)
    done = pyqtSignal(object, str, bool)  # devices, error, cancelled

    def __init__(self, mode: str, subnets: list[str] | None, parent=None):
        super().__init__(parent)
        self._mode = mode
        self._subnets = subnets

    def cancel(self) -> None:
        self.requestInterruption()

    def run(self) -> None:
        try:
            def progress(message: str, current: int, total: int) -> None:
                self.progress.emit(message, current, total)

            if self._mode == "local":
                scan_and_connect_local(
                    progress_cb=progress,
                    cancel_check=self.isInterruptionRequested,
                )
            else:
                scan_and_connect_wireless(
                    progress_cb=progress,
                    subnets=self._subnets,
                    cancel_check=self.isInterruptionRequested,
                )
            if self.isInterruptionRequested():
                self.done.emit([], "", True)
                return
            devices = list_adb_devices(
                cancel_check=self.isInterruptionRequested)
            self.done.emit(devices, "", self.isInterruptionRequested())
        except BaseException as exc:  # noqa: BLE001 - 后台任务必须恢复 UI
            logger.exception("扫描 ADB 设备失败")
            self.done.emit([], f"{type(exc).__name__}: {exc}", False)


class DeviceScanPanel(QWidget):
    """局域网与本机模拟器扫描 UI；扫描执行由宿主接入。"""

    scan_requested = pyqtSignal(str, object)  # mode, subnets
    cancel_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scanning = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        hint = QLabel(tr(
            "局域网扫描用于发现已开启无线 ADB 的设备；"
            "本机端口扫描用于发现只监听 127.0.0.1 的模拟器。\n"
            "扫描只建立 ADB 连接，不会切换主界面的执行目标。"))
        hint.setWordWrap(True)
        layout.addWidget(hint)

        controls = QHBoxLayout()
        controls.addWidget(QLabel(tr("扫描网段：")))
        self.subnet_combo = AutoWidthComboBox()
        self.subnet_combo.addItem(tr("全部网卡（默认）"), None)
        try:
            interfaces = list_ipv4_interfaces()
        except Exception as exc:  # noqa: BLE001 - 网卡枚举失败不阻止本机扫描
            logger.warning(f"枚举本机网卡失败: {exc}")
            interfaces = []
        for interface in interfaces:
            self.subnet_combo.addItem(interface.label, [interface.subnet])
        controls.addWidget(self.subnet_combo, 1)

        self.scan_lan_button = QPushButton(tr("扫描局域网"))
        self.scan_lan_button.clicked.connect(
            lambda: self._begin_scan("lan"))
        self.scan_local_button = QPushButton(tr("扫描本机端口"))
        self.scan_local_button.clicked.connect(
            lambda: self._begin_scan("local"))
        self.cancel_button = QPushButton(tr("取消扫描"))
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel_scan)
        apply_button_style(self.scan_lan_button)
        apply_button_style(
            self.scan_local_button, self.cancel_button, variant="neutral")
        fit_button_width(
            self.scan_lan_button, self.scan_local_button, self.cancel_button)
        controls.addWidget(self.scan_lan_button)
        controls.addWidget(self.scan_local_button)
        controls.addWidget(self.cancel_button)
        layout.addLayout(controls)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        self.status_label = QLabel(tr("尚未扫描"))
        self.status_label.setStyleSheet("color: gray;")
        layout.addWidget(self.status_label)

        self.result_list = QTreeWidget()
        self.result_list.setHeaderLabels([tr("设备"), "Serial"])
        self.result_list.setRootIsDecorated(False)
        header = self.result_list.header()
        if header is not None:
            header.setStretchLastSection(True)
        layout.addWidget(self.result_list, 1)

    @property
    def scanning(self) -> bool:
        return self._scanning

    def selected_subnets(self) -> list[str] | None:
        value = self.subnet_combo.currentData()
        return list(value) if isinstance(value, list) else None

    def _begin_scan(self, mode: str) -> None:
        if self._scanning:
            return
        self._scanning = True
        self.scan_lan_button.setEnabled(False)
        self.scan_local_button.setEnabled(False)
        self.subnet_combo.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.status_label.setText(
            tr("正在扫描本机模拟器端口…") if mode == "local"
            else tr("正在扫描局域网设备…"))
        self.scan_requested.emit(mode, self.selected_subnets())

    def _cancel_scan(self) -> None:
        if not self._scanning:
            return
        self.cancel_button.setEnabled(False)
        self.status_label.setText(tr("正在取消扫描…"))
        self.cancel_requested.emit()

    def update_progress(self, message: str, current: int, total: int) -> None:
        self.progress_bar.setRange(0, max(total, 0))
        self.progress_bar.setValue(max(current, 0))
        self.status_label.setText(message)

    def finish(
        self, devices: list[dict], *, error: str = "", cancelled: bool = False,
    ) -> None:
        self._scanning = False
        self.scan_lan_button.setEnabled(True)
        self.scan_local_button.setEnabled(True)
        self.subnet_combo.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.result_list.clear()
        for device in devices:
            serial = str(device.get("serial") or "")
            model = str(device.get("model") or "")
            self.result_list.addTopLevelItem(QTreeWidgetItem([
                model or tr("未知设备"), serial,
            ]))
        if cancelled:
            self.status_label.setText(tr("扫描已取消"))
        elif error:
            self.status_label.setText(
                tr("扫描失败：{error}").format(error=error))
        elif devices:
            self.status_label.setText(
                tr("已发现并连接 {count} 台设备").format(count=len(devices)))
        else:
            self.status_label.setText(tr("未发现可连接的设备"))
