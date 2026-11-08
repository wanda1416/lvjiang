"""Android 画面流健康模型测试（简化版：仅 EOF + 用户停止）。

真实事故：看门狗的 stall/rx_idle 检测在正常静止画面上误报，
导致能正常截屏的任务被强制中断。现已删除全部主动探测，
只保留两个零误报信号：socket EOF 和用户主动停止。
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

import numpy as np
import pytest

from lvjiang.core.android.recovery import AndroidRecovery, ChannelFaultError
from lvjiang.core.android.scrcpy_capture import (
    FAULT_SOCKET_EOF,
    AndroidStreamCapture,
)
from lvjiang.core.config.models import AndroidRecoveryConfig


class _AliveThread:
    def __init__(self, alive: bool = True):
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


class _EofSocket:
    def recv(self, _n: int) -> bytes:
        return b""

    def close(self) -> None:
        pass


def _stream(*, alive: bool = True) -> AndroidStreamCapture:
    cap = AndroidStreamCapture(MagicMock())
    cap._running = True
    cap._user_stop = False
    cap._decode_thread = _AliveThread(alive)
    cap._transitioning = False
    cap._sock = _EofSocket()
    return cap


def test_socket_eof_declares_fault_once():
    seen: list[tuple[str, str]] = []
    cap = _stream()
    cap.set_on_stream_lost(lambda kind, detail: seen.append((kind, detail)))

    cap._decode_loop()

    assert cap.last_fault == FAULT_SOCKET_EOF
    assert len(seen) == 1


def test_user_stop_does_not_declare_fault():
    stopped = _stream()
    stopped._user_stop = True
    stopped._decode_loop()
    assert stopped.last_fault == ""


def test_decode_failure_does_not_declare_fault():
    """解码失败只计数，不声明故障（宁漏报不误报）。"""
    cap = _stream()
    for _ in range(100):
        cap._note_decode_error()
    assert cap.last_fault == ""


def test_frame_to_bgr_converts_real_frame():
    """真实事故回归：_frame_to_bgr 丢 self 导致解码线程启动即崩，
    首帧永远不出、连接必然失败。用真实 av 帧走一遍转换路径。"""
    import av

    cap = _stream()
    frame = av.VideoFrame(4, 4, format="yuv420p")
    bgr = cap._frame_to_bgr(frame)
    assert bgr.shape == (4, 4, 3)
    assert bgr.dtype == np.uint8


def test_decode_loop_crash_is_recorded_not_silent():
    """解码线程崩溃必须记录 _decode_thread_error，
    让 start() 首帧等待报真实原因而不是误导性的息屏提示。"""
    cap = _stream()

    def _boom(*_a):
        raise TypeError("_frame_to_bgr() takes 1 positional argument but 2 were given")

    cap._decode_loop_inner = _boom
    cap._decode_loop()
    assert cap._decode_thread_error is not None
    assert "TypeError" in cap._decode_thread_error


def test_capture_blocking_never_returns_stale_frame_on_fault():
    cap = _stream(alive=False)
    cap._latest_frame = np.zeros((2, 2, 3), dtype=np.uint8)
    cap._fault = FAULT_SOCKET_EOF
    cap._fault_detail = "连接断开"

    def give_up(_s):
        raise ChannelFaultError("用户停止")

    cap.set_stream_recovery(give_up)
    with pytest.raises(ChannelFaultError):
        cap.capture_blocking(timeout=0.1)

    def recover(s):
        s._fault = ""
        s._decode_thread = _AliveThread(True)

    cap.set_stream_recovery(recover)
    cap.capture_blocking(timeout=0.1)


def test_recover_stream_retries_then_waits_and_starts_new_round():
    device = MagicMock()
    device.recovery = AndroidRecoveryConfig(retry_interval=0, stream_attempts=2)
    device.resume_event = threading.Event()
    device.stop_check = None
    device.can_wait_for_recovery = lambda: True
    prompts: list[str] = []
    device.on_connection_lost = lambda message: (prompts.append(message),
                                                 device.resume_event.set())
    coordinator = AndroidRecovery(device)
    calls: list[int] = []

    class _Stream:
        last_fault_detail = "连接断开"
        def restart(self):
            calls.append(1)
            return len(calls) >= 3
        def clear_fault(self):
            pass

    coordinator.recover_stream(_Stream())
    assert len(calls) == 3
    assert len(prompts) == 1


def test_recover_stream_without_manual_channel_raises_channel_fault():
    device = MagicMock()
    device.recovery = AndroidRecoveryConfig(retry_interval=0, stream_attempts=1)
    device.resume_event = threading.Event()
    device.stop_check = None
    device.can_wait_for_recovery = lambda: True
    device.on_connection_lost = None
    coordinator = AndroidRecovery(device)

    class _Stream:
        last_fault_detail = "连接断开"
        def restart(self):
            return False
        def clear_fault(self):
            pass

    with pytest.raises(ChannelFaultError):
        coordinator.recover_stream(_Stream())
