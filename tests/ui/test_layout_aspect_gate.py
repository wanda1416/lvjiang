"""画布尺寸门禁：布局声明了宽高比，画面形态对不上就不许启动。

形态错配（端游全屏选了窗口布局、设备不是 20:9）不会报任何错，只会让所有坐标
整体偏移——现象是「脚本点了没反应」，用户几乎不可能自己定位到布局选错。所以在
启动前按布局声明的画布宽高比校验一次，不符合就拒绝执行并说明原因。

容差按相对误差算：画布是比例、窗口边框却是固定像素，同一个布局在不同分辨率下会
有小幅漂移，这部分必须放过；真正的形态错配是 2% 以上的量级，两者不会混。
"""

from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QComboBox, QWidget

from lvjiang.core.layout_models import CanvasConfig, Layout
from lvjiang.ui.main.run_control import RunControlMixin

pytestmark = pytest.mark.usefixtures('qapp')

#: 桌面布局的画布（客户区口径）与随包一致
_DESKTOP_CANVAS = CanvasConfig(
    x_ratio=8 / 1936, y_ratio=31 / 1119,
    w_ratio=1920 / 1936, h_ratio=1080 / 1119)


class _Capture:
    def __init__(self, size):
        self._size = size

    def get_capture_size(self):
        return self._size


class _Manager:
    def __init__(self, layout):
        self._layout = layout

    def load_layout(self, key):
        return self._layout if key == self._layout.key else None


class Host(QWidget, RunControlMixin):
    def __init__(self, layout, capture_size):
        super().__init__()
        self.layout_combo = QComboBox(self)
        self.layout_combo.addItem(layout.name, layout.key)
        self._layout_manager = _Manager(layout)
        self._capture = _Capture(capture_size)
        self._user_config = SimpleNamespace()


def _host(*, aspect="16:9", tolerance=0.015, canvas=None, size=(1936, 1119)):
    layout = Layout(key='desktop', name='桌面布局',
                    canvas=canvas or _DESKTOP_CANVAS,
                    aspect=aspect, aspect_tolerance=tolerance)
    return Host(layout, size)


class TestPasses:
    def test_calibration_resolution(self, qtbot):
        host = _host(size=(1936, 1119))       # 1920x1080 客户区
        qtbot.addWidget(host)
        assert host._layout_aspect_error() == ""

    def test_other_resolutions_within_tolerance(self, qtbot):
        """2560x1440 与 3840x2160 的漂移来自固定像素边框，必须放过。"""
        for size in ((2576, 1479), (3856, 2199), (1296, 759)):
            host = _host(size=size)
            qtbot.addWidget(host)
            assert host._layout_aspect_error() == "", size

    def test_no_declaration_means_no_check(self, qtbot):
        host = _host(aspect="", canvas=CanvasConfig(), size=(1000, 1000))
        qtbot.addWidget(host)
        assert host._layout_aspect_error() == ""

    def test_missing_capture_size_is_not_a_failure(self, qtbot):
        """还没定位窗口时拿不到尺寸——门禁只拦已知的错配，不拦「还不知道」。"""
        host = _host(size=(0, 0))
        qtbot.addWidget(host)
        assert host._layout_aspect_error() == ""


class TestBlocks:
    def test_fullscreen_picture_with_windowed_layout(self, qtbot):
        """全屏截图配窗口布局：画布少裁了标题栏那一块，偏差约 2.7%。"""
        host = _host(size=(1920, 1080))
        qtbot.addWidget(host)
        message = host._layout_aspect_error()
        assert "当前布局画布区域尺寸不符合预定义要求，请修改布局后重试" in message
        assert "16:9" in message

    def test_windowed_picture_with_fullscreen_layout(self, qtbot):
        host = _host(canvas=CanvasConfig(), size=(1936, 1119),
                     tolerance=0.005)
        qtbot.addWidget(host)
        assert host._layout_aspect_error() != ""

    def test_wrong_device_aspect(self, qtbot):
        """19.5:9 手机配 20:9 布局，偏差 2.5%。"""
        host = _host(aspect="20:9", tolerance=0.005,
                     canvas=CanvasConfig(), size=(2340, 1080))
        qtbot.addWidget(host)
        assert host._layout_aspect_error() != ""

    def test_message_reports_actual_geometry(self, qtbot):
        host = _host(canvas=CanvasConfig(), size=(2560, 1600),
                     tolerance=0.005)
        qtbot.addWidget(host)
        message = host._layout_aspect_error()
        assert "2560×1600" in message      # 画布实际尺寸
        assert "10.00%" in message         # 偏差
        assert "0.50%" in message          # 容差
