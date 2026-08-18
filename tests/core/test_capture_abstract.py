"""截图后端抽象测试

验证：
1. 默认 capture_to_file / set_capture_region / attach_to_window 行为
2. 桌面工厂 create_capture_backend 返回 DesktopCapture 实例
3. ADB 工厂 create_capture_backend 返回 AdbCapture 实例
"""

from lvjiang.core.capture_base import CaptureBackend

# ─── fake 子类 ────────────────────────────────────────────────

class _FakeCapture(CaptureBackend):
    """最小实现：返回固定尺寸和可选固定图像"""

    def __init__(self, size=(1080, 1920), frame=None):
        self._size = size
        self._frame = frame
        self.region = None
        self.attached = None

    def capture(self, timeout=5.0):
        return self._frame

    def get_capture_size(self):
        return self._size


# ─── 默认方法行为 ─────────────────────────────────────────────

def test_default_set_capture_region_is_noop():
    # 基类默认 no-op：调用不抛异常、不改变状态
    fake = _FakeCapture()
    fake.set_capture_region(10, 20, 300, 400)
    # 尺寸仍为初始值
    assert fake.get_capture_size() == (1080, 1920)


def test_default_attach_to_window_returns_false():
    fake = _FakeCapture()
    assert fake.attach_to_window("some_window") is False


def test_default_stop_is_noop():
    fake = _FakeCapture()
    fake.stop()  # 默认实现不抛异常
    assert fake.get_capture_size() == (1080, 1920)  # 状态未变


def test_default_capture_to_file_returns_false_when_capture_fails():
    # capture 返回 None → capture_to_file 返回 False
    fake = _FakeCapture(frame=None)
    assert fake.capture_to_file("unused.png") is False


# ─── 桌面工厂 ─────────────────────────────────────────────────

def test_desktop_capture_factory():
    from lvjiang.core.desktop import DesktopCapture, create_capture_backend
    backend = create_capture_backend()
    assert isinstance(backend, DesktopCapture)
    assert isinstance(backend, CaptureBackend)
    # 清理工作线程
    backend.stop()
    assert not backend._worker.is_alive()


# ─── ADB 工厂 ────────────────────────────────────────────────

def test_adb_factory_returns_capture_backend():
    from lvjiang.core.android import AdbCapture, create_capture_backend

    class _FakeDevice:
        def get_resolution(self): return (1080, 1920)
        def get_abi(self): return "arm64-v8a"
        def get_sdk(self): return "30"
        def remove_forward(self, spec): pass
        def shell(self, cmd): return b""

    # 不实际启动流，仅验证工厂返回类型
    backend = create_capture_backend(device=_FakeDevice())
    assert isinstance(backend, AdbCapture)
    assert isinstance(backend, CaptureBackend)
    # 清理（未启动，stop 应安全）
    backend.stop()
