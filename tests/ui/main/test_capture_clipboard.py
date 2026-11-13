"""预览复制必须保留原始分辨率和颜色，不能复制缩略图或重新抓帧。"""
import numpy as np
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QStatusBar, QWidget

from lvjiang.ui.main.capture_ops import CaptureOpsMixin


class Host(QWidget, CaptureOpsMixin):
    def __init__(self):
        super().__init__()
        self._device_ready = True
        self._target_window = None
        self._scrcpy_streaming = False
        self.image = None
        self.bar = QStatusBar(self)
        self.panel = self._build_capture_panel()
        self.panel.setParent(self)

    def _get_last_capture(self):
        return self.image

    def statusBar(self):
        return self.bar


def test_copy_original_image_preserves_size_colors_and_owns_memory(qtbot):
    host = Host()
    qtbot.addWidget(host)
    source = np.zeros((3, 8, 3), dtype=np.uint8)
    source[:, :, :] = (12, 34, 56)
    host.image = source[:, ::2]  # 非连续帧也应能正常复制。
    host._apply_rec_state()
    host.btn_copy_capture.click()
    clipboard = QApplication.clipboard()
    assert clipboard is not None
    copied = clipboard.image()
    assert (copied.width(), copied.height()) == (4, 3)
    assert copied.pixelColor(0, 0) == QColor(56, 34, 12)
    source[:] = 0
    assert clipboard.image().pixelColor(0, 0) == QColor(56, 34, 12)
    clipboard.clear()


def test_copy_without_frame_is_disabled_and_does_not_clear_clipboard(qtbot):
    host = Host()
    qtbot.addWidget(host)
    clipboard = QApplication.clipboard()
    assert clipboard is not None
    clipboard.setText("keep existing clipboard")
    assert not host.btn_copy_capture.isEnabled()
    host._on_copy_capture()
    assert clipboard.text() == "keep existing clipboard"
    clipboard.clear()
