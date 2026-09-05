"""输入后端抽象测试

验证：
1. 输入模拟参数注入（_inject_input_sim 默认值 / 自定义值）
2. 桌面工厂 create_input_backend 按 mode 返回正确子类
3. AdbInput 构造与输入模拟参数注入
"""

import pytest

from lvjiang.core.config import InputSimConfig
from lvjiang.core.input_base import InputBackend

# ─── fake 子类 ────────────────────────────────────────────────

class _FakeInput(InputBackend):
    """最小实现"""

    def __init__(self, input_sim=None):
        self._inject_input_sim(self, input_sim)
        self.background_mode = False
        self.target_hwnd = None

    def click_screen(self, screen_x, screen_y, poi_name="", *, button="left"):
        pass

    def place_screen(self, screen_x, screen_y, poi_name=""):
        pass

    def move_screen(self, screen_x, screen_y, poi_name="", duration=None):
        pass

    def move_relative(self, delta_x, delta_y, poi_name="", duration=None):
        pass

    def scroll_screen(self, screen_x, screen_y, direction="down", amount=1, poi_name="", *, interval=None):
        pass

    def drag_screen(self, from_x, from_y, to_x, to_y,
                    poi_name="", duration=None, hold=None):
        pass

    def key_down(self, key: str) -> None:
        pass

    def key_up(self, key: str) -> None:
        pass


# ─── 输入模拟参数注入 ─────────────────────────────────────────

def test_inject_input_sim_defaults():
    fake = _FakeInput()
    # 默认值与 InputSimConfig 默认值一致
    assert fake.before_click_wait == (0.1, 0.3)
    assert fake.after_click_wait == (0.1, 0.2)
    assert fake.mouse_move_duration == (0.3, 0.6)
    assert fake.click_random_offset == 3
    assert 0 <= fake.region_jitter_ratio < 0.5


def test_inject_input_sim_custom():
    cfg = InputSimConfig(before_click_wait=(0.5, 1.0), click_random_offset=10)
    fake = _FakeInput(input_sim=cfg)
    assert fake.before_click_wait == (0.5, 1.0)
    assert fake.click_random_offset == 10
    # 未指定的字段仍取默认
    assert fake.after_click_wait == (0.1, 0.2)


# ─── 桌面工厂 ─────────────────────────────────────────────────

def test_desktop_factory_post_mode():
    from lvjiang.core.desktop import PostMessageInput, create_input_backend
    backend = create_input_backend(mode="post")
    assert isinstance(backend, PostMessageInput)
    assert isinstance(backend, InputBackend)
    assert backend.background_mode is True


def test_desktop_factory_send_mode():
    from lvjiang.core.desktop import SendInputInput, create_input_backend
    backend = create_input_backend(mode="send")
    assert isinstance(backend, SendInputInput)
    assert isinstance(backend, InputBackend)
    assert backend.background_mode is False


def test_desktop_factory_unknown_mode_raises():
    from lvjiang.core.desktop import create_input_backend
    with pytest.raises(ValueError):
        create_input_backend(mode="bogus")


def test_desktop_factory_with_hwnd():
    from lvjiang.core.desktop import PostMessageInput, create_input_backend
    backend = create_input_backend(mode="post", hwnd=0xABCD)
    assert isinstance(backend, PostMessageInput)
    assert backend.target_hwnd == 0xABCD


# ─── AdbInput 输入模拟参数注入 ────────────────────────────────

def test_adb_input_sim_injection():
    from lvjiang.core.android.input import AdbInput

    class _FakeDevice:
        def shell(self, *a, **kw): return ""

    cfg = InputSimConfig(click_random_offset=7)
    adb = AdbInput(device=_FakeDevice(), input_sim=cfg)
    assert adb.click_random_offset == 7


def test_adb_factory_returns_input_backend():
    from lvjiang.core.android import create_input_backend

    class _FakeDevice:
        def shell(self, *a, **kw): return ""

    backend = create_input_backend(device=_FakeDevice())
    assert isinstance(backend, InputBackend)
