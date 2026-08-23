"""全局热键回调门控测试

主窗口只注册全局热键 F8-F10，且回调须经 _backend_ready() 门控：
未定位窗口/未连接设备时直接忽略，不发射信号。
不实例化 MainWindow、不启动 pynput listener，用桩对象直调
未绑定方法验证门控逻辑。
"""

from lvjiang.ui.main_window import MainWindow


class _Signal:
    """信号桩：记录 emit 次数"""

    def __init__(self):
        self.count = 0

    def emit(self):
        self.count += 1


class _Stub:
    """MainWindow 热键回调所需的最小属性集"""

    def __init__(self, ready: bool, running: bool = False):
        self._ready = ready
        self._running = running
        self.started = 0
        self.f9_pressed = _Signal()
        self.f10_pressed = _Signal()
        self.pause_resume_count = 0
        self._on_global_f8 = lambda: None
        self._on_global_f9 = lambda: None
        self._on_global_f10 = lambda: None

    def _backend_ready(self) -> bool:
        return self._ready

    def _on_start(self):
        self.started += 1

    def _on_pause_resume(self):
        self.pause_resume_count += 1


class TestGlobalHotkeyGating:
    def test_main_listener_does_not_register_f12(self):
        stub = _Stub(ready=True)
        bindings = MainWindow._main_global_hotkey_bindings(stub)
        assert set(bindings) == {"<f8>", "<f9>", "<f10>"}

    def test_not_ready_ignores_all(self):
        stub = _Stub(ready=False)
        MainWindow._on_global_f9(stub)
        MainWindow._on_global_f10(stub)
        MainWindow._on_global_f8(stub)
        assert stub.f9_pressed.count == 0
        assert stub.f10_pressed.count == 0
        assert stub.pause_resume_count == 0

    def test_ready_emits_signals(self):
        stub = _Stub(ready=True)
        MainWindow._on_global_f9(stub)
        MainWindow._on_global_f10(stub)
        MainWindow._on_global_f8(stub)
        assert stub.f9_pressed.count == 1
        assert stub.f10_pressed.count == 1
        assert stub.pause_resume_count == 1  # F8 触发暂停/恢复


class TestF9StartEntry:
    def test_starts_when_idle(self):
        stub = _Stub(ready=True, running=False)
        MainWindow._on_f9_start(stub)
        assert stub.started == 1

    def test_ignored_when_running(self):
        stub = _Stub(ready=True, running=True)
        MainWindow._on_f9_start(stub)
        assert stub.started == 0
