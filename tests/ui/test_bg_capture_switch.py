"""后台截图开关只在「后台模式」之下出现。

两者必须成对：前台输入（SendInput）要求游戏窗口在前台，这时后台截图没有意义；
真正危险的是反过来——只开后台截图、输入仍是前台，用户以为可以把窗口盖起来，
一盖输入就失效，而且现象是「脚本点了没反应」，很难自己定位到这个组合上。
所以取消后台模式时必须连带把后台截图关掉，这里把这条规则钉住。
"""

from PyQt6.QtWidgets import QCheckBox, QWidget

from lvjiang.ui.main.window_ops import WindowOpsMixin


class _Host(WindowOpsMixin, QWidget):
    def __init__(self, backend="windows"):
        super().__init__()
        self._backend = backend
        self._running = False
        self._target_window = None
        self._capture = None
        self.chk_bg_mode = QCheckBox(self)
        self.chk_bg_capture = QCheckBox(self)
        # 真实窗口里这两个控件初始都是隐藏的
        self.chk_bg_mode.setVisible(False)
        self.chk_bg_capture.setVisible(False)


def _host(qtbot, backend="windows"):
    host = _Host(backend)
    qtbot.addWidget(host)
    host.show()          # isVisible 只有在父窗口显示后才有意义
    return host


def test_hidden_while_foreground_input(qtbot):
    host = _host(qtbot)
    host.chk_bg_mode.setChecked(False)
    host._refresh_bg_capture_visibility()
    assert not host.chk_bg_capture.isVisible()


def test_shown_once_background_input_is_on(qtbot):
    host = _host(qtbot)
    host.chk_bg_mode.setChecked(True)
    host._refresh_bg_capture_visibility()
    assert host.chk_bg_capture.isVisible()
    assert host.chk_bg_capture.isEnabled()


def test_leaving_background_mode_also_clears_background_capture(qtbot):
    """这条是关键：不能留下「前台输入 + 后台截图」的组合。"""
    host = _host(qtbot)
    host.chk_bg_mode.setChecked(True)
    host._refresh_bg_capture_visibility()
    host.chk_bg_capture.setChecked(True)

    host.chk_bg_mode.setChecked(False)
    host._refresh_bg_capture_visibility()

    assert not host.chk_bg_capture.isChecked()
    assert not host.chk_bg_capture.isVisible()


def test_hidden_in_device_mode(qtbot):
    """安卓截图本就来自设备，不存在遮挡问题，开关不该出现。"""
    host = _host(qtbot, backend="adb")
    host.chk_bg_mode.setChecked(True)
    host._refresh_bg_capture_visibility()
    assert not host.chk_bg_capture.isVisible()


def test_locked_while_running(qtbot):
    """运行中换截图后端会把正在用的实例停掉，必须锁死。"""
    host = _host(qtbot)
    host.chk_bg_mode.setChecked(True)
    host._running = True
    host._refresh_bg_capture_visibility()
    assert host.chk_bg_capture.isVisible()
    assert not host.chk_bg_capture.isEnabled()
