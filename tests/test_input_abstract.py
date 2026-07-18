"""输入后端抽象测试

验证：
1. InputBackend 抽象契约（ABC 不可直接实例化、子类必须实现 click_screen/drag_screen）
2. 桌面工厂 create_input_backend 按 mode 返回正确子类
3. AdbInput / SendInputInput / PostMessageInput 的构造与延迟参数注入
4. 工作流只依赖抽象接口（fake 子类可无缝注入 BaseWorkflow）
"""

import pytest

from lvjiang.config import DelayConfig
from lvjiang.core.input_base import InputBackend


# ─── fake 子类 ────────────────────────────────────────────────

class _FakeInput(InputBackend):
    """最小实现：记录 click_screen / drag_screen 调用"""

    def __init__(self, delay_config=None):
        self._inject_delay_config(self, delay_config)
        self.background_mode = False
        self.target_hwnd = None
        self.calls = []

    def click_screen(self, screen_x, screen_y, poi_name=""):
        self.calls.append(("click", screen_x, screen_y, poi_name))

    def drag_screen(self, from_x, from_y, to_x, to_y,
                    poi_name="", duration=None, hold=None):
        self.calls.append(("drag", from_x, from_y, to_x, to_y, poi_name, duration, hold))


class _IncompleteInput(InputBackend):
    """故意缺失抽象方法实现，用于验证 ABC 契约"""
    pass


# ─── ABC 契约 ────────────────────────────────────────────────

def test_input_backend_cannot_be_instantiated():
    with pytest.raises(TypeError):
        InputBackend()


def test_incomplete_subclass_cannot_be_instantiated():
    with pytest.raises(TypeError):
        _IncompleteInput()


def test_complete_subclass_can_be_instantiated():
    fake = _FakeInput()
    assert isinstance(fake, InputBackend)


# ─── 延迟参数注入 ─────────────────────────────────────────────

def test_inject_delay_config_defaults():
    fake = _FakeInput()
    # 默认值与 DelayConfig 默认值一致
    assert fake.before_click_wait == (0.1, 0.3)
    assert fake.after_click_wait == (0.1, 0.2)
    assert fake.mouse_move_duration == (0.3, 0.6)
    assert fake.click_random_offset == 3
    assert 0 <= fake.region_jitter_ratio < 0.5


def test_inject_delay_config_custom():
    cfg = DelayConfig(before_click_wait=(0.5, 1.0), click_random_offset=10)
    fake = _FakeInput(delay_config=cfg)
    assert fake.before_click_wait == (0.5, 1.0)
    assert fake.click_random_offset == 10
    # 未指定的字段仍取默认
    assert fake.after_click_wait == (0.1, 0.2)


# ─── 调用转发 ─────────────────────────────────────────────────

def test_click_screen_dispatch():
    fake = _FakeInput()
    fake.click_screen(100, 200, "poi")
    assert fake.calls == [("click", 100, 200, "poi")]


def test_drag_screen_dispatch():
    fake = _FakeInput()
    fake.drag_screen(10, 20, 30, 40, "arr", duration=0.5, hold=0.2)
    assert fake.calls == [("drag", 10, 20, 30, 40, "arr", 0.5, 0.2)]


# ─── 桌面工厂 ─────────────────────────────────────────────────

def test_desktop_factory_post_mode():
    from lvjiang.core.desktop import create_input_backend, PostMessageInput
    backend = create_input_backend(mode="post")
    assert isinstance(backend, PostMessageInput)
    assert isinstance(backend, InputBackend)
    assert backend.background_mode is True


def test_desktop_factory_send_mode():
    from lvjiang.core.desktop import create_input_backend, SendInputInput
    backend = create_input_backend(mode="send")
    assert isinstance(backend, SendInputInput)
    assert isinstance(backend, InputBackend)
    assert backend.background_mode is False


def test_desktop_factory_unknown_mode_raises():
    from lvjiang.core.desktop import create_input_backend
    with pytest.raises(ValueError):
        create_input_backend(mode="bogus")


def test_desktop_factory_with_hwnd():
    from lvjiang.core.desktop import create_input_backend, PostMessageInput
    backend = create_input_backend(mode="post", hwnd=0xABCD)
    assert isinstance(backend, PostMessageInput)
    assert backend.target_hwnd == 0xABCD


# ─── AdbInput 抽象一致性 ──────────────────────────────────────

def test_adb_input_is_input_backend():
    from lvjiang.core.android.input import AdbInput

    class _FakeDevice:
        def shell(self, *a, **kw): return ""

    adb = AdbInput(device=_FakeDevice())
    assert isinstance(adb, InputBackend)
    assert adb.background_mode is True
    assert adb.target_hwnd is None


def test_adb_input_delay_injection():
    from lvjiang.core.android.input import AdbInput

    class _FakeDevice:
        def shell(self, *a, **kw): return ""

    cfg = DelayConfig(click_random_offset=7)
    adb = AdbInput(device=_FakeDevice(), delay_config=cfg)
    assert adb.click_random_offset == 7


def test_adb_factory_returns_input_backend():
    from lvjiang.core.android import create_input_backend

    class _FakeDevice:
        def shell(self, *a, **kw): return ""

    backend = create_input_backend(device=_FakeDevice())
    assert isinstance(backend, InputBackend)


# ─── 工作流可接受 fake 后端 ────────────────────────────────────

def test_base_workflow_accepts_fake_input_backend():
    """验证 BaseWorkflow 只依赖抽象接口，fake 子类可无缝注入"""
    from lvjiang.core.region_config import Layout, CanvasConfig
    from lvjiang.workflows.base import BaseWorkflow

    class _FakeCapture:
        def get_capture_size(self): return (1080, 1920)
        def capture(self, timeout=5.0): return None

    fake_input = _FakeInput()
    wf = BaseWorkflow(
        capture=_FakeCapture(),
        ocr=None,
        input_ctrl=fake_input,
        layout=Layout(name="t"),
    )
    wf._layout.set_canvas(CanvasConfig())
    wf._input.click_screen(100, 200, "wf_test")
    assert fake_input.calls == [("click", 100, 200, "wf_test")]
