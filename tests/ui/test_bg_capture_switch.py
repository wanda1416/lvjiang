"""后台截图开关：Windows 模式下常驻，不可用时整体禁用并给出原因。

它跟着「后台模式」走。两者必须成对：前台输入（SendInput）要求游戏窗口在前台，
这时后台截图没有意义；真正危险的是反过来——只开后台截图、输入仍是前台，用户以为
可以把窗口盖起来，一盖输入就失效，而且现象是「脚本点了没反应」，很难自己定位到
这个组合上。所以取消后台模式时必须连带把后台截图关掉，这里把这条规则钉住。

**禁用而不是隐藏**：随勾选凭空冒出来会让整排控件跳位；而且「功能存在但当前不可用」
按项目惯例就该禁用并给原因，隐藏留给「压根不适用于当前环境」——安卓设备模式那种。

**默认值只在刚可用时套一次**：配置里的「窗口截图」是本次运行的初值，不能在任务
起停时把用户手动取消的勾选又打回来。
"""

from dataclasses import dataclass

import pytest
from PyQt6.QtWidgets import QCheckBox, QWidget

from lvjiang.ui.main.execution_targets import (
    AndroidConnectionDraft,
    WindowConnectionDraft,
)
from lvjiang.ui.main.window_ops import WindowOpsMixin


@dataclass
class _Config:
    desktop_background_capture: bool = False


class _Host(WindowOpsMixin, QWidget):
    def __init__(self, backend="windows", capture_default=False):
        super().__init__()
        self._backend = backend
        self._candidate_backend = backend
        self._running = False
        self._target_window = None
        self._capture = None
        self._user_config = _Config(capture_default)
        self._window_connection_draft = WindowConnectionDraft()
        self._android_connection_draft = AndroidConnectionDraft()
        self.chk_bg_mode = QCheckBox(self)
        self.chk_bg_capture = QCheckBox(self)
        self.chk_scrcpy = QCheckBox(self)
        self.chk_agent = QCheckBox(self)
        self.chk_red_box = QCheckBox(self)
        # 真实窗口里这两个控件初始都是隐藏的
        self.chk_bg_mode.setVisible(False)
        self.chk_bg_capture.setVisible(False)


@pytest.fixture
def wgc_ok(monkeypatch):
    """把组件可用性钉成可用：这些用例测的是开关逻辑，不是平台支持情况。"""
    import lvjiang.core.desktop as desktop
    monkeypatch.setattr(desktop, "wgc_available", lambda: (True, ""))


def _host(qtbot, backend="windows", capture_default=False):
    host = _Host(backend, capture_default)
    qtbot.addWidget(host)
    host.show()          # isVisible 只有在父窗口显示后才有意义
    return host


def test_disabled_not_hidden_while_foreground_input(qtbot, wgc_ok):
    """前台输入时禁用并给出原因，不是让它消失——消失会让整排控件跳位。"""
    host = _host(qtbot)
    host._window_connection_draft.background_input = False
    host._refresh_bg_capture_visibility()
    assert host.chk_bg_capture.isVisible()
    assert not host.chk_bg_capture.isEnabled()
    assert "后台模式" in host.chk_bg_capture.toolTip()


def test_usable_once_background_input_is_on(qtbot, wgc_ok):
    host = _host(qtbot)
    host._window_connection_draft.background_input = True
    host._refresh_bg_capture_visibility()
    assert host.chk_bg_capture.isVisible()
    assert host.chk_bg_capture.isEnabled()


def test_leaving_background_mode_also_clears_background_capture(qtbot, wgc_ok):
    """这条是关键：不能留下「前台输入 + 后台截图」的组合。"""
    host = _host(qtbot)
    host._on_bg_mode_changed(True)
    host._on_bg_capture_changed(True)

    host._on_bg_mode_changed(False)

    # 勾选被清掉（关键），但控件仍在原位、只是禁用
    assert not host.chk_bg_capture.isChecked()
    assert host.chk_bg_capture.isVisible()
    assert not host.chk_bg_capture.isEnabled()


def test_connection_draft_does_not_mutate_connected_target(qtbot, wgc_ok):
    """右侧开关只准备下一次定位，不能偷偷改动已连接目标。"""
    host = _host(qtbot)
    connected_input = object()
    connected_capture = object()
    host._input = connected_input
    host._capture = connected_capture
    host._target_window = {"hwnd": 1}

    host._on_bg_mode_changed(True)
    host._on_bg_capture_changed(True)

    assert host._window_connection_draft.background_input is True
    assert host._window_connection_draft.background_capture is True
    assert host._input is connected_input
    assert host._capture is connected_capture


def test_switching_candidate_kind_preserves_both_drafts(qtbot, wgc_ok):
    """切换扫描类型只切展示，不把另一类连接草稿改回当前目标状态。"""
    host = _host(qtbot)
    host._window_connection_draft.background_input = True
    host._window_connection_draft.background_capture = True
    host._android_connection_draft.capture_method = "scrcpy"
    host._android_connection_draft.device_execution = True

    host._refresh_connection_draft_ui("adb")
    assert host.chk_scrcpy.isChecked()
    assert host.chk_agent.isChecked()

    host._refresh_connection_draft_ui("windows")
    assert host.chk_bg_mode.isChecked()
    assert host.chk_bg_capture.isChecked()
    assert host._android_connection_draft.capture_method == "scrcpy"
    assert host._android_connection_draft.device_execution is True


def test_hidden_in_device_mode(qtbot, wgc_ok):
    """安卓截图本就来自设备，压根不适用——这种才该隐藏。"""
    host = _host(qtbot, backend="adb")
    host._refresh_bg_capture_visibility()
    assert not host.chk_bg_capture.isVisible()


def test_locked_while_running(qtbot, wgc_ok):
    """运行中换截图后端会把正在用的实例停掉，必须锁死。"""
    host = _host(qtbot)
    host._window_connection_draft.background_input = True
    host._running = True
    host._refresh_bg_capture_visibility()
    assert host.chk_bg_capture.isVisible()
    assert not host.chk_bg_capture.isEnabled()


def test_disabled_with_reason_when_component_unavailable(qtbot, monkeypatch):
    """组件缺失时也禁用并写明原因，不等用户勾选后才报错。"""
    import lvjiang.core.desktop as desktop
    monkeypatch.setattr(
        desktop, "wgc_available", lambda: (False, "后台截图仅支持 Windows"))
    host = _host(qtbot, capture_default=True)
    host._window_connection_draft.background_input = True
    host._refresh_bg_capture_visibility()
    host._apply_bg_capture_default()
    assert host.chk_bg_capture.isVisible()
    assert not host.chk_bg_capture.isEnabled()
    assert not host.chk_bg_capture.isChecked()
    assert "仅支持 Windows" in host.chk_bg_capture.toolTip()


class TestDefaultFromConfig:
    """配置里的「窗口截图」是本次运行的初值"""

    def test_applied_when_background_mode_turns_on(self, qtbot, wgc_ok):
        """勾上后台模式，后台截图立刻可用并按配置跟上。"""
        host = _host(qtbot, capture_default=True)
        host._window_connection_draft.background_input = True
        host._refresh_bg_capture_visibility()
        host._apply_bg_capture_default()
        assert host.chk_bg_capture.isEnabled()
        assert host.chk_bg_capture.isChecked()

    def test_not_applied_while_foreground_input(self, qtbot, wgc_ok):
        """即使配置里设了后台截图，前台输入下也不勾——它此刻不可用。"""
        host = _host(qtbot, capture_default=True)
        host._window_connection_draft.background_input = False
        host._refresh_bg_capture_visibility()
        host._apply_bg_capture_default()
        assert not host.chk_bg_capture.isEnabled()
        assert not host.chk_bg_capture.isChecked()

    def test_foreground_default_leaves_it_off(self, qtbot, wgc_ok):
        host = _host(qtbot, capture_default=False)
        host._window_connection_draft.background_input = True
        host._refresh_bg_capture_visibility()
        host._apply_bg_capture_default()
        assert host.chk_bg_capture.isEnabled()
        assert not host.chk_bg_capture.isChecked()

    def test_manual_opt_out_survives_task_restart(self, qtbot, wgc_ok):
        """本次运行内手动取消后，任务起停不能把默认值又打回来。"""
        host = _host(qtbot, capture_default=True)
        host._window_connection_draft.background_input = True
        host._refresh_bg_capture_visibility()
        host._apply_bg_capture_default()
        host._on_bg_capture_changed(False)      # 用户手动改回前台截图

        host._running = True
        host._refresh_bg_capture_visibility()
        host._running = False
        host._refresh_bg_capture_visibility()

        assert not host.chk_bg_capture.isChecked()
