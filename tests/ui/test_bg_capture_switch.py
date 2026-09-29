"""后台截图开关：Windows 模式下常驻，未开后台模式时禁用。

两者必须成对：前台输入（SendInput）要求游戏窗口在前台，这时后台截图没有意义；
真正危险的是反过来——只开后台截图、输入仍是前台，用户以为可以把窗口盖起来，
一盖输入就失效，而且现象是「脚本点了没反应」，很难自己定位到这个组合上。
所以取消后台模式时必须连带把后台截图关掉，这里把这条规则钉住。

**禁用而不是隐藏**：随勾选凭空冒出来会让整排控件跳位；而且「功能存在但当前不可用」
按项目惯例就该禁用并给原因，隐藏留给「压根不适用于当前环境」——安卓设备模式那种。
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


def test_disabled_not_hidden_while_foreground_input(qtbot):
    """前台输入时禁用并给出原因，不是让它消失——消失会让整排控件跳位。"""
    host = _host(qtbot)
    host.chk_bg_mode.setChecked(False)
    host._refresh_bg_capture_visibility()
    assert host.chk_bg_capture.isVisible()
    assert not host.chk_bg_capture.isEnabled()
    assert "后台模式" in host.chk_bg_capture.toolTip()


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

    # 勾选被清掉（关键），但控件仍在原位、只是禁用
    assert not host.chk_bg_capture.isChecked()
    assert host.chk_bg_capture.isVisible()
    assert not host.chk_bg_capture.isEnabled()


def test_hidden_in_device_mode(qtbot):
    """安卓截图本就来自设备，压根不适用——这种才该隐藏。"""
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
