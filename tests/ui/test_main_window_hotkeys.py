"""全局热键回调门控测试

全局热键仅 F8-F10，且回调须经 _backend_ready() 门控：
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
        self.f8_pressed = _Signal()

    def _backend_ready(self) -> bool:
        return self._ready

    def _on_start(self):
        self.started += 1


class TestGlobalHotkeyGating:
    def test_not_ready_ignores_all(self):
        stub = _Stub(ready=False)
        MainWindow._on_global_f9(stub)
        MainWindow._on_global_f10(stub)
        MainWindow._on_global_f8(stub)
        assert stub.f9_pressed.count == 0
        assert stub.f10_pressed.count == 0
        assert stub.f8_pressed.count == 0

    def test_ready_emits_signals(self):
        stub = _Stub(ready=True)
        MainWindow._on_global_f9(stub)
        MainWindow._on_global_f10(stub)
        MainWindow._on_global_f8(stub)
        assert stub.f9_pressed.count == 1
        assert stub.f10_pressed.count == 1
        assert stub.f8_pressed.count == 1


class TestF9StartEntry:
    def test_starts_when_idle(self):
        stub = _Stub(ready=True, running=False)
        MainWindow._on_f9_start(stub)
        assert stub.started == 1

    def test_ignored_when_running(self):
        stub = _Stub(ready=True, running=True)
        MainWindow._on_f9_start(stub)
        assert stub.started == 0
