"""截图后端抽象测试

验证：
1. CaptureBackend 抽象契约（ABC 不可直接实例化、子类必须实现 capture/get_capture_size）
2. 默认 capture_to_file / set_capture_region / attach_to_window 行为
3. 桌面工厂 create_capture_backend 返回 DesktopCapture 实例
4. AdbCapture 实现 CaptureBackend 接口
5. 工作流可接受 fake CaptureBackend
"""

import numpy as np
import pytest

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


class _IncompleteCapture(CaptureBackend):
    """故意缺失抽象方法实现"""
    pass


# ─── ABC 契约 ────────────────────────────────────────────────

def test_capture_backend_cannot_be_instantiated():
    with pytest.raises(TypeError):
        CaptureBackend()


def test_incomplete_subclass_cannot_be_instantiated():
    with pytest.raises(TypeError):
        _IncompleteCapture()


def test_complete_subclass_can_be_instantiated():
    fake = _FakeCapture()
    assert isinstance(fake, CaptureBackend)


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
    try:
        backend._worker.join(timeout=0.1)
    except Exception:
        pass


def test_desktop_capture_is_capture_backend():
    from lvjiang.core.desktop import DesktopCapture
    assert issubclass(DesktopCapture, CaptureBackend)


# ─── AdbCapture 抽象一致性 ────────────────────────────────────

def test_adb_capture_is_capture_backend_class():
    from lvjiang.core.android.adb_capture import AdbCapture
    assert issubclass(AdbCapture, CaptureBackend)


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


# ─── 工作流可接受 fake 后端 ────────────────────────────────────

def test_base_workflow_accepts_fake_capture_backend():
    """验证 BaseWorkflow 只依赖抽象接口，fake 子类可无缝注入"""
    from lvjiang.core.scene_registry import CanvasConfig, Layout
    from lvjiang.workflows.base import BaseWorkflow

    class _FakeInput:
        background_mode = False
        target_hwnd = None
        def click_screen(self, x, y, poi=""): pass
        def drag_screen(self, *a, **kw): pass

    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    fake_capture = _FakeCapture(size=(200, 100), frame=frame)
    wf = BaseWorkflow(
        capture=fake_capture,
        ocr=None,
        input_ctrl=_FakeInput(),
        layout=Layout(name="t"),
        window_left=0,
        window_top=0,
    )
    wf._layout.set_canvas(CanvasConfig())
    # 验证 get_capture_size 通过抽象接口被调用
    w, h = wf._capture.get_capture_size()
    assert (w, h) == (200, 100)
    # 验证 capture 通过抽象接口被调用
    img = wf._capture.capture()
    assert img is frame
