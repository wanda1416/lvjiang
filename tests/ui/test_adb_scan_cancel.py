"""ADB 扫描取消状态机不阻塞 UI。"""

from lvjiang.ui.main.window_ops import WindowOpsMixin


class _Button:
    def __init__(self):
        self.enabled = True
        self.text = ""

    def setEnabled(self, value):
        self.enabled = value

    def setText(self, value):
        self.text = value


class _StatusBar:
    def __init__(self):
        self.message = ""

    def showMessage(self, value):
        self.message = value


class _Worker:
    _task = "wireless_scan"

    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def is_cancelled(self):
        return self.cancelled


class _Thread:
    def isRunning(self):
        return True


class _Host(WindowOpsMixin):
    def __init__(self):
        self._device_worker = _Worker()
        self._device_thread = _Thread()
        self.btn_scan_window = _Button()
        self.btn_scan_window.setEnabled(False)
        self.btn_scan_device = _Button()
        self._status_bar = _StatusBar()

    def statusBar(self):
        return self._status_bar


def test_cancel_scan_changes_state_without_waiting_for_thread():
    host = _Host()

    host._cancel_device_scan()

    assert host._device_worker.cancelled is True
    assert host.btn_scan_window.enabled is False
    assert host.btn_scan_device.enabled is False
    assert host.btn_scan_device.text == "取消中..."
    assert host._status_bar.message == "正在取消扫描..."


def test_cancelled_worker_completion_restores_scan_button():
    host = _Host()
    host._device_worker.cancel()
    host.btn_scan_device.setEnabled(False)

    host._on_device_worker_finished(host._device_worker)

    assert host.btn_scan_device.enabled is True
    assert host.btn_scan_window.enabled is True
    assert host.btn_scan_device.text == "扫描设备"
    assert host._status_bar.message == "已取消扫描"
