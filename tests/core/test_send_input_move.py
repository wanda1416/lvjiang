"""SendInput 相对移动的坐标换算与整数分步测试。"""

from unittest.mock import MagicMock

from lvjiang.core.desktop import send_input as send_input_module
from lvjiang.core.desktop.send_input import SendInputInput


def test_distribute_preserves_signed_total():
    positive = SendInputInput._distribute(7, 4)
    negative = SendInputInput._distribute(-7, 4)

    assert sum(positive) == 7
    assert sum(negative) == -7
    assert len(positive) == len(negative) == 4


def test_distribute_zero_duration_single_step():
    assert SendInputInput._distribute(12, 1) == [12]
    assert SendInputInput._distribute(-5, 0) == [-5]


def test_move_to_converts_current_cursor_to_relative_input(monkeypatch):
    class _User32:
        @staticmethod
        def GetCursorPos(pointer):
            pointer._obj.x = 300
            pointer._obj.y = 200
            return True

    backend = SendInputInput()
    monkeypatch.setattr(send_input_module, "_user32", _User32())
    monkeypatch.setattr(backend, "_activate_target", MagicMock())
    send_steps = MagicMock()
    monkeypatch.setattr(backend, "_send_relative_steps", send_steps)

    backend.move_screen(450, 120, duration=0.25)

    send_steps.assert_called_once_with(150, -80, 0.25)


def test_relative_steps_preserve_exact_requested_vector(monkeypatch):
    backend = SendInputInput()
    events = []
    monkeypatch.setattr(
        send_input_module,
        "send_mouse_event",
        lambda _flags, dx, dy: events.append((dx, dy)),
    )
    monkeypatch.setattr(send_input_module.time, "sleep", lambda _delay: None)

    backend._send_relative_steps(17, -9, 0.04)

    assert len(events) == 4
    assert sum(dx for dx, _ in events) == 17
    assert sum(dy for _, dy in events) == -9
