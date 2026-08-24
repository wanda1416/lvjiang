"""SendInput 点击的鼠标键分发测试（click_screen/_click 的 button 参数）。"""

from unittest.mock import MagicMock

from lvjiang.core.desktop import send_input as send_input_module
from lvjiang.core.desktop.send_input import SendInputInput
from lvjiang.core.desktop.win32_util import (
    _MOUSEEVENTF_LEFTDOWN,
    _MOUSEEVENTF_LEFTUP,
    _MOUSEEVENTF_MIDDLEDOWN,
    _MOUSEEVENTF_MIDDLEUP,
    _MOUSEEVENTF_RIGHTDOWN,
    _MOUSEEVENTF_RIGHTUP,
    _MOUSEEVENTF_XDOWN,
    _MOUSEEVENTF_XUP,
    _XBUTTON1,
    _XBUTTON2,
)


def _make_backend(monkeypatch):
    backend = SendInputInput()
    monkeypatch.setattr(backend, "_activate_target", MagicMock())
    monkeypatch.setattr(backend, "_move_to", MagicMock())
    monkeypatch.setattr(send_input_module, "_user32", MagicMock())
    monkeypatch.setattr(send_input_module.time, "sleep", lambda _s: None)
    events = []
    monkeypatch.setattr(
        send_input_module, "send_mouse_event",
        lambda flags, dx=0, dy=0, mouse_data=0: events.append((flags, mouse_data)))
    return backend, events


def test_click_screen_defaults_to_left_button(monkeypatch):
    backend, events = _make_backend(monkeypatch)
    backend.click_screen(10, 10, "test")
    assert events == [(_MOUSEEVENTF_LEFTDOWN, 0), (_MOUSEEVENTF_LEFTUP, 0)]


def test_click_screen_right_button(monkeypatch):
    backend, events = _make_backend(monkeypatch)
    backend.click_screen(10, 10, "test", button="right")
    assert events == [(_MOUSEEVENTF_RIGHTDOWN, 0), (_MOUSEEVENTF_RIGHTUP, 0)]


def test_click_screen_middle_button(monkeypatch):
    backend, events = _make_backend(monkeypatch)
    backend.click_screen(10, 10, "test", button="middle")
    assert events == [(_MOUSEEVENTF_MIDDLEDOWN, 0), (_MOUSEEVENTF_MIDDLEUP, 0)]


def test_click_screen_side_buttons_carry_xbutton_id(monkeypatch):
    """侧键点击要带正确的 mouseData 区分 XBUTTON1/XBUTTON2。"""
    backend, events = _make_backend(monkeypatch)

    backend.click_screen(10, 10, "test", button="x1")
    assert events == [(_MOUSEEVENTF_XDOWN, _XBUTTON1), (_MOUSEEVENTF_XUP, _XBUTTON1)]

    events.clear()
    backend.click_screen(10, 10, "test", button="x2")
    assert events == [(_MOUSEEVENTF_XDOWN, _XBUTTON2), (_MOUSEEVENTF_XUP, _XBUTTON2)]


def test_click_screen_unknown_button_falls_back_to_left(monkeypatch):
    """理论上不会发生（语法层已限定枚举），但后端兜底不应崩溃。"""
    backend, events = _make_backend(monkeypatch)
    backend.click_screen(10, 10, "test", button="mouse6")
    assert events == [(_MOUSEEVENTF_LEFTDOWN, 0), (_MOUSEEVENTF_LEFTUP, 0)]
