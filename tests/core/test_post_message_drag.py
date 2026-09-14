"""PostMessage 后台拖拽的 duration 与 hold 时间语义。"""

from unittest.mock import MagicMock

from lvjiang.core.config import InputSimConfig
from lvjiang.core.desktop import post_message as post_module
from lvjiang.core.desktop import win32_util
from lvjiang.core.desktop.post_message import PostMessageInput


def test_backend_forwards_drag_duration_and_hold(monkeypatch):
    backend = PostMessageInput(
        input_sim=InputSimConfig(
            before_click_wait=(0, 0),
            after_click_wait=(0, 0),
        ),
        hwnd=123,
    )
    send = MagicMock()
    monkeypatch.setattr(post_module, "postmessage_drag", send)
    monkeypatch.setattr(
        post_module, "screen_to_client_logical",
        lambda _hwnd, x, y: (x, y),
    )
    monkeypatch.setattr(post_module, "precise_wait", lambda _s: True)

    backend.drag_screen(10, 20, 30, 40, duration=0.3, hold=1.4)

    send.assert_called_once_with(
        123, 10, 20, 30, 40,
        duration=0.3,
        hold=1.4,
        activate=False,
    )


def test_background_move_uses_absolute_deadlines_after_position_is_known(
    monkeypatch,
):
    backend = PostMessageInput(hwnd=123)
    backend._last_client_pos = (10, 20)
    sent: list[tuple[int, int]] = []
    deadlines: list[int] = []
    monkeypatch.setattr(
        post_module, "screen_to_client_logical",
        lambda _hwnd, x, y: (x, y),
    )
    monkeypatch.setattr(
        post_module, "postmessage_move",
        lambda _hwnd, x, y, *, activate: sent.append((x, y)),
    )
    monkeypatch.setattr(post_module.time, "perf_counter_ns", lambda: 1_000)
    monkeypatch.setattr(
        post_module, "precise_wait_until",
        lambda deadline, *, spin_tail_ns: (
            deadlines.append(deadline) or True),
    )

    backend.move_screen(30, 40, duration=0.02)

    assert sent == [(20, 30), (30, 40)]
    assert deadlines == [10_001_000, 20_001_000]
    assert backend._last_client_pos == (30, 40)


def test_win32_drag_uses_absolute_movement_deadlines_and_holds(monkeypatch):
    user32 = MagicMock()
    waits: list[float] = []
    deadlines: list[tuple[int, int]] = []
    monkeypatch.setattr(win32_util, "_user32", user32)
    monkeypatch.setattr(
        win32_util, "resolve_message_target", lambda hwnd, _x, _y: hwnd)
    monkeypatch.setattr(win32_util, "make_lparam", lambda _x, _y: 99)
    monkeypatch.setattr(win32_util.time, "perf_counter_ns", lambda: 1_000)
    monkeypatch.setattr(
        win32_util, "precise_wait",
        lambda seconds: waits.append(seconds) or True)
    monkeypatch.setattr(
        win32_util, "precise_wait_until",
        lambda deadline, *, spin_tail_ns: (
            deadlines.append((deadline, spin_tail_ns)) or True))

    win32_util.postmessage_drag(
        123, 10, 20, 30, 40,
        duration=0.1,
        hold=1.4,
        steps=5,
    )

    assert waits == [0.03, 0.05, 1.4]
    assert deadlines == [
        (20_001_000, 0),
        (40_001_000, 0),
        (60_001_000, 0),
        (80_001_000, 0),
        (100_001_000, 0),
    ]
    assert user32.PostMessageW.call_count == 8
