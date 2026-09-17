"""SendInput 相对移动的坐标换算与整数分步测试。"""

import threading
import time
from unittest.mock import MagicMock

from lvjiang.core.desktop import send_input as send_input_module
from lvjiang.core.desktop.send_input import SendInputInput
from lvjiang.core.desktop.win32_util import (
    _MOUSEEVENTF_XDOWN,
    _MOUSEEVENTF_XUP,
    _XBUTTON1,
    _XBUTTON2,
)
from lvjiang.core.input_trace import InputTrace, InputTraceEvent


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
    monkeypatch.setattr(
        send_input_module, "precise_wait_until", lambda *_a, **_k: True)

    backend._send_relative_steps(17, -9, 0.04)

    assert len(events) == 4
    assert sum(dx for dx, _ in events) == 17
    assert sum(dy for _, dy in events) == -9


def test_drag_duration_only_applies_to_pressed_movement(monkeypatch):
    backend = SendInputInput()
    monkeypatch.setattr(backend, "_activate_target", MagicMock())
    monkeypatch.setattr(backend, "_move_to", MagicMock())
    monkeypatch.setattr(send_input_module, "precise_wait", lambda _s, **_kw: True)
    moves = MagicMock()
    monkeypatch.setattr(send_input_module, "smooth_move_to", moves)
    monkeypatch.setattr(send_input_module, "send_mouse_event", MagicMock())

    backend.drag_screen(
        10, 20, 30, 40,
        duration=0.25,
        pre_delay=(0, 0),
        post_delay=(0, 0),
    )

    backend._move_to.assert_called_once_with(10, 20)
    moves.assert_called_once_with(30, 40, 0.25)


def test_trace_replay_preserves_order_and_scaled_cumulative_path(monkeypatch):
    backend = SendInputInput()
    monkeypatch.setattr(backend, "_activate_target", MagicMock())
    monkeypatch.setattr(
        backend, "_wait_trace_deadline", MagicMock(return_value=False))
    mouse_events = []
    monkeypatch.setattr(
        send_input_module,
        "send_mouse_event",
        lambda flags, dx=0, dy=0: mouse_events.append((flags, dx, dy)),
    )
    trace = InputTrace(
        1000,
        800,
        (
            InputTraceEvent(0, "move", (1, 1)),
            InputTraceEvent(1000, "move", (1, 0)),
            InputTraceEvent(2000, "move", (-1, -1)),
        ),
    )

    backend.replay_input_trace(
        trace,
        canvas_width=1500,
        canvas_height=400,
        stop_check=lambda: False,
    )

    assert [(dx, dy) for _, dx, dy in mouse_events] == [
        (2, 0),
        (1, 0),
        (-1, 0),
    ]
    assert all(flags & 0x2000 for flags, _, _ in mouse_events)


def test_trace_replay_sends_xbutton_data_for_side_buttons(monkeypatch):
    """侧键（前进/后退）回放要带正确的 mouseData 区分 XBUTTON1/XBUTTON2。"""
    backend = SendInputInput()
    monkeypatch.setattr(backend, "_activate_target", MagicMock())
    monkeypatch.setattr(
        backend, "_wait_trace_deadline", MagicMock(return_value=False))
    calls = []
    monkeypatch.setattr(
        send_input_module,
        "send_mouse_event",
        lambda flags, dx=0, dy=0, mouse_data=0: calls.append(
            (flags, mouse_data)),
    )
    trace = InputTrace(
        1000,
        800,
        (
            InputTraceEvent(0, "button", ("x1", True)),
            InputTraceEvent(1000, "button", ("x1", False)),
            InputTraceEvent(2000, "button", ("x2", True)),
            InputTraceEvent(3000, "button", ("x2", False)),
        ),
    )

    backend.replay_input_trace(
        trace, canvas_width=1000, canvas_height=800, stop_check=lambda: False)

    assert calls == [
        (_MOUSEEVENTF_XDOWN, _XBUTTON1),
        (_MOUSEEVENTF_XUP, _XBUTTON1),
        (_MOUSEEVENTF_XDOWN, _XBUTTON2),
        (_MOUSEEVENTF_XUP, _XBUTTON2),
    ]


def test_trace_replay_releases_held_side_button_on_stop(monkeypatch):
    """回放中途停止时，仍按下的侧键必须在 finally 里补一次松开。"""
    backend = SendInputInput()
    monkeypatch.setattr(backend, "_activate_target", MagicMock())
    calls = []
    monkeypatch.setattr(
        send_input_module,
        "send_mouse_event",
        lambda flags, dx=0, dy=0, mouse_data=0: calls.append(
            (flags, mouse_data)),
    )
    # 第一个事件（按下 x1）派发之后立刻停止：第二个事件（松开）永远不会
    # 被派发到，只能靠 finally 里的兜底松开逻辑。
    trace = InputTrace(
        1000, 800,
        (
            InputTraceEvent(0, "button", ("x1", True)),
            InputTraceEvent(1_000_000, "button", ("x1", False)),
        ),
    )

    backend.replay_input_trace(
        trace, canvas_width=1000, canvas_height=800,
        stop_check=lambda: len(calls) >= 1)

    assert (_MOUSEEVENTF_XDOWN, _XBUTTON1) in calls
    assert (_MOUSEEVENTF_XUP, _XBUTTON1) in calls


def test_wait_trace_deadline_interrupted_by_pause_before_deadline(monkeypatch):
    """长间隔等待期间 pause_event 被 clear：不能等到 deadline 才响应。"""
    monkeypatch.setattr(send_input_module.time, "sleep", lambda _s: None)
    pause_event = threading.Event()  # 未 set = 暂停中
    deadline_ns = time.perf_counter_ns() + 5_000_000_000  # 5s 之后

    interrupted = SendInputInput._wait_trace_deadline(
        deadline_ns, stop_check=lambda: False, pause_event=pause_event)

    assert interrupted is True


def test_wait_trace_deadline_reaches_deadline_when_running():
    pause_event = threading.Event()
    pause_event.set()  # 运行中，不应被当成暂停打断
    deadline_ns = time.perf_counter_ns() - 1  # 已过期，立即到达

    interrupted = SendInputInput._wait_trace_deadline(
        deadline_ns, stop_check=lambda: False, pause_event=pause_event)

    assert interrupted is False


def test_wait_trace_deadline_remembers_pause_across_immediate_resume(
    monkeypatch,
):
    """暂停触发退出后即使马上恢复，也不能误报 deadline 已到。"""
    pause_event = threading.Event()  # clear = 暂停

    def wait_until(_deadline, *, stop_check, spin_tail_ns):
        assert spin_tail_ns == 200_000
        assert stop_check() is True
        pause_event.set()  # 模拟检查返回后紧接着恢复
        return False

    monkeypatch.setattr(send_input_module, "precise_wait_until", wait_until)

    interrupted = SendInputInput._wait_trace_deadline(
        time.perf_counter_ns() + 1_000_000_000,
        stop_check=lambda: False,
        pause_event=pause_event,
    )

    assert interrupted is True


def test_trace_replay_defers_events_while_paused(monkeypatch):
    """暂停中收到的长间隔事件不会提前触发，只在 stop 后才安全退出。"""
    backend = SendInputInput()
    monkeypatch.setattr(backend, "_activate_target", MagicMock())
    sent = []
    monkeypatch.setattr(
        send_input_module, "send_mouse_event",
        lambda *a, **k: sent.append((a, k)))

    pause_event = threading.Event()  # 保持未 set：全程处于暂停状态
    calls = {"n": 0}

    def stop_check():
        calls["n"] += 1
        return calls["n"] > 3  # 模拟暂停几轮后用户点了停止

    trace = InputTrace(1000, 800, (InputTraceEvent(10_000_000, "move", (5, 5)),))

    backend.replay_input_trace(
        trace, canvas_width=1000, canvas_height=800,
        stop_check=stop_check, pause_event=pause_event)

    assert sent == []
