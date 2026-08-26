""""后台模式"锁定态 + "红框标定"开关测试

覆盖点：
1. 后台模式 checkbox 仅在 Windows 定位后才受"任务是否运行"门控——
   定位后依然可以再次切换（无需断连重新定位），只在任务运行期间锁定，
   任务结束后自动恢复。
2. 红框标定 checkbox 是纯运行期状态（不读写 _user_config），
   取消勾选立即隐藏边框，勾选且已定位则立即按当前窗口位置重新画出。
"""

from lvjiang.ui.main.window_ops import WindowOpsMixin


class _CheckBox:
    """QCheckBox 桩：只记录可见性/可用性/勾选态"""

    def __init__(self, visible=True, checked=True):
        self._visible = visible
        self._enabled = True
        self._checked = checked

    def isVisible(self):
        return self._visible

    def setVisible(self, v):
        self._visible = v

    def isEnabled(self):
        return self._enabled

    def setEnabled(self, v):
        self._enabled = v

    def isChecked(self):
        return self._checked

    def setChecked(self, v):
        self._checked = v


class _Overlay:
    """BorderOverlay 桩：只记录调用"""

    def __init__(self):
        self.shown_rect = None
        self.color = None
        self.hidden = False

    def show_border(self, left, top, width, height):
        self.shown_rect = (left, top, width, height)
        self.hidden = False

    def set_color(self, color):
        self.color = color

    def hide_border(self):
        self.hidden = True
        self.shown_rect = None


class _WindowOpsStub(WindowOpsMixin):
    """WindowOpsMixin 状态机方法所需的最小属性集"""

    def __init__(self, backend="windows", target_window=None, running=False):
        self._backend = backend
        self._target_window = target_window
        self._running = running
        self.chk_bg_mode = _CheckBox(visible=True)
        self.chk_red_box = _CheckBox(visible=True, checked=True)
        self._overlay = _Overlay()


_WINDOW = {"hwnd": 1, "left": 10, "top": 20, "width": 800, "height": 600}


class TestBgModeLock:
    def test_locked_after_locate_while_running(self):
        stub = _WindowOpsStub(target_window=_WINDOW, running=True)
        stub._refresh_bg_mode_lock()
        assert stub.chk_bg_mode.isEnabled() is False

    def test_unlocked_after_locate_when_idle(self):
        stub = _WindowOpsStub(target_window=_WINDOW, running=False)
        stub.chk_bg_mode.setEnabled(False)  # 模拟任务刚结束前的锁定态
        stub._refresh_bg_mode_lock()
        assert stub.chk_bg_mode.isEnabled() is True

    def test_relocks_when_task_starts_then_unlocks_when_it_ends(self):
        stub = _WindowOpsStub(target_window=_WINDOW, running=False)
        stub._refresh_bg_mode_lock()
        assert stub.chk_bg_mode.isEnabled() is True

        stub._running = True
        stub._refresh_bg_mode_lock()
        assert stub.chk_bg_mode.isEnabled() is False

        stub._running = False
        stub._refresh_bg_mode_lock()
        assert stub.chk_bg_mode.isEnabled() is True

    def test_no_target_window_leaves_enabled_state_untouched(self):
        stub = _WindowOpsStub(target_window=None, running=False)
        stub.chk_bg_mode.setEnabled(False)
        stub._refresh_bg_mode_lock()
        assert stub.chk_bg_mode.isEnabled() is False  # 未定位：调用方自行控制，这里不覆盖

    def test_adb_backend_leaves_enabled_state_untouched(self):
        stub = _WindowOpsStub(backend="adb", target_window=_WINDOW, running=True)
        stub.chk_bg_mode.setEnabled(True)
        stub._refresh_bg_mode_lock()
        assert stub.chk_bg_mode.isEnabled() is True  # 非 windows 后端不受此逻辑管辖

    def test_invisible_checkbox_is_skipped(self):
        stub = _WindowOpsStub(target_window=_WINDOW, running=True)
        stub.chk_bg_mode.setVisible(False)
        stub.chk_bg_mode.setEnabled(True)
        stub._refresh_bg_mode_lock()
        assert stub.chk_bg_mode.isEnabled() is True  # 隐藏时不出手


class TestRedBoxToggle:
    def test_unchecking_hides_border(self):
        stub = _WindowOpsStub(target_window=_WINDOW)
        stub._overlay.show_border(*[_WINDOW[k] for k in ("left", "top", "width", "height")])
        stub._on_red_box_changed(0)
        assert stub._overlay.hidden is True

    def test_rechecking_while_located_redraws_border(self):
        stub = _WindowOpsStub(target_window=_WINDOW)
        stub._on_red_box_changed(0)
        assert stub._overlay.hidden is True

        stub._on_red_box_changed(2)  # Qt.CheckState.Checked == 2
        assert stub._overlay.shown_rect == (10, 20, 800, 600)
        assert stub._overlay.color == "red"

    def test_rechecking_without_target_window_draws_nothing(self):
        stub = _WindowOpsStub(target_window=None)
        stub._on_red_box_changed(2)
        assert stub._overlay.shown_rect is None
        assert stub._overlay.hidden is False
