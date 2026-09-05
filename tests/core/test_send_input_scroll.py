"""SendInput 滚轮的逐格发送与 interval 间隔测试。

覆盖 scroll_screen 的两个关键行为：
- amount=N 逐格发 N 条独立滚轮事件（而非一条大 delta）
- interval 指定时用固定间隔取代默认 20~50ms 随机间隔
"""

from unittest.mock import MagicMock

from lvjiang.core.desktop import send_input as send_input_module
from lvjiang.core.desktop.send_input import SendInputInput


def _make_scroll_backend(monkeypatch):
    backend = SendInputInput()
    monkeypatch.setattr(backend, "_activate_target", MagicMock())
    monkeypatch.setattr(backend, "_move_to", MagicMock())
    wheels = []
    monkeypatch.setattr(
        send_input_module, "send_mouse_wheel_event",
        lambda delta: wheels.append(delta))
    sleeps = []
    monkeypatch.setattr(
        send_input_module.time, "sleep", lambda s: sleeps.append(s))
    return backend, wheels, sleeps


def test_scroll_sends_one_wheel_event_per_notch(monkeypatch):
    """amount=5 逐格发 5 条独立滚轮事件，而非一条大 delta。"""
    backend, wheels, _sleeps = _make_scroll_backend(monkeypatch)
    backend.scroll_screen(100, 100, "down", 5)
    assert len(wheels) == 5
    # down = 负 delta（sign=-1 × WHEEL_DELTA）
    assert all(d < 0 for d in wheels)


def test_scroll_up_direction_is_positive_delta(monkeypatch):
    backend, wheels, _sleeps = _make_scroll_backend(monkeypatch)
    backend.scroll_screen(100, 100, "up", 2)
    assert len(wheels) == 2
    assert all(d > 0 for d in wheels)


def test_scroll_default_interval_is_random_20_to_50ms(monkeypatch):
    """未指定 interval 时，逐格之间用 20~50ms 随机间隔。"""
    backend, _wheels, sleeps = _make_scroll_backend(monkeypatch)
    backend.scroll_screen(100, 100, "down", 4)
    # 4 格之间有 3 段间隔
    assert len(sleeps) == 3
    assert all(0.02 <= s <= 0.05 for s in sleeps)


def test_scroll_explicit_interval_overrides_random(monkeypatch):
    """interval=0.1 时，逐格之间固定 sleep 0.1，取代随机间隔。"""
    backend, _wheels, sleeps = _make_scroll_backend(monkeypatch)
    backend.scroll_screen(100, 100, "up", 3, interval=0.1)
    # 3 格之间 2 段间隔，且都是固定 0.1
    assert sleeps == [0.1, 0.1]


def test_scroll_single_notch_has_no_interval_sleep(monkeypatch):
    """只滚 1 格时没有格间间隔，即使指定了 interval。"""
    backend, wheels, sleeps = _make_scroll_backend(monkeypatch)
    backend.scroll_screen(100, 100, "down", 1, interval=0.1)
    assert len(wheels) == 1
    assert sleeps == []
