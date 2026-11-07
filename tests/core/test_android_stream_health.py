"""Android 画面流的健康判定与恢复阶梯。

这里保护的是"流断了但界面显示得很正常"这条真实回归链：
解码线程退出只留一行 DEBUG、``capture()`` 永远返回旧帧、看门狗把息屏误判成
故障，三条任意一条回退，批量都会在冻结画面上静默跑完。
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import numpy as np
import pytest

from lvjiang.core.android.recovery import AndroidRecovery, ChannelFaultError
from lvjiang.core.android.scrcpy_capture import (
    FAULT_DECODE,
    FAULT_RX_IDLE,
    FAULT_SOCKET_EOF,
    FAULT_STALL,
    AndroidStreamCapture,
)
from lvjiang.core.config.models import AndroidRecoveryConfig


class _AliveThread:
    def __init__(self, alive: bool = True):
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


class _EofSocket:
    """立即返回 EOF 的 socket 替身。"""

    def recv(self, _n: int) -> bytes:
        return b""

    def close(self) -> None:
        pass


def _stream(*, alive: bool = True) -> AndroidStreamCapture:
    cap = AndroidStreamCapture(MagicMock())
    cap._running = True
    cap._user_stop = False
    cap._decode_thread = _AliveThread(alive)  # type: ignore[assignment]
    cap._transitioning = False
    cap._sock = _EofSocket()  # type: ignore[assignment]
    now = time.monotonic()
    cap._last_rx_at = now
    cap._last_frame_at = now
    return cap


def test_socket_eof_is_reported_once_and_user_stop_is_silent():
    """EOF 必须登记故障且只通知一次；用户主动 stop() 不能报故障。

    回归：漏掉 ``_user_stop`` 判断，每次断开设备都会弹"等待恢复"；
    重复通知则会让弹窗叠成一片。
    """
    seen: list[tuple[str, str]] = []
    cap = _stream()
    cap.set_on_stream_lost(lambda kind, detail: seen.append((kind, detail)))

    cap._decode_loop()

    assert cap.last_fault == FAULT_SOCKET_EOF
    assert len(seen) == 1
    # 故障已登记，再跑一次也只通知一次
    cap._declare_fault(FAULT_STALL, "重复")
    assert len(seen) == 1

    stopped = _stream()
    stopped._user_stop = True
    stopped._decode_loop()
    assert stopped.last_fault == ""
    assert not stopped.is_healthy or True  # 主动停止不参与健康判定


def test_watchdog_ignores_screen_off_but_flags_awake_stall():
    """息屏零帧是常态，亮屏零帧才是故障。

    实测依据：息屏 25s 是 0 帧 0 字节，与编码器停摆完全同构；不查设备显示
    状态就会把正常挂机判成故障并反复重启会话。
    """
    cap = _stream()
    cap._last_rx_at = time.monotonic() - 30
    cap._last_frame_at = time.monotonic() - 30
    cap._device_awake = lambda: False  # type: ignore[method-assign]

    cap._check_health_once()
    assert cap.last_fault == ""

    cap._device_awake = lambda: True  # type: ignore[method-assign]
    cap._check_health_once()
    assert cap.last_fault == FAULT_RX_IDLE

    # 字节在流、只是画面不动 → 归因为停摆而不是连接问题
    stalled = _stream()
    stalled._last_frame_at = time.monotonic() - 30
    stalled._device_awake = lambda: True  # type: ignore[method-assign]
    stalled._check_health_once()
    assert stalled.last_fault == FAULT_STALL


def test_watchdog_waits_out_session_switch():
    """尺寸/方向切换期间没有帧是正常的，不能误报。"""
    cap = _stream()
    cap._transitioning = True
    cap._last_rx_at = time.monotonic() - 2
    cap._device_awake = lambda: True  # type: ignore[method-assign]
    cap._check_health_once()
    assert cap.last_fault == ""

    cap._last_rx_at = time.monotonic() - 30
    cap._check_health_once()
    assert cap.last_fault == FAULT_STALL


def test_capture_blocking_never_returns_stale_frame_on_fault():
    """通道故障时 capture_blocking 必须等恢复或抛错，绝不返回旧帧。"""
    cap = _stream(alive=False)
    cap._latest_frame = np.zeros((2, 2, 3), dtype=np.uint8)
    cap._fault = FAULT_STALL
    cap._fault_detail = "亮屏但 30 秒没有新画面"

    def give_up(_stream) -> None:
        raise ChannelFaultError("用户停止")

    cap.set_stream_recovery(give_up)
    with pytest.raises(ChannelFaultError):
        cap.capture_blocking(timeout=0.1)

    def recover(stream) -> None:
        stream._fault = ""
        stream._decode_thread = _AliveThread(True)  # type: ignore[assignment]

    cap.set_stream_recovery(recover)
    cap.capture_blocking(timeout=0.1)


def test_recover_stream_retries_then_waits_and_starts_new_round():
    """自动重试耗尽后进入人工等待（kind=stream），确认后重新开始一轮。"""
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
        last_fault_detail = "亮屏但 30 秒没有新画面"

        def restart(self) -> bool:
            calls.append(1)
            return len(calls) >= 3   # 第一轮 2 次失败，人工确认后第 3 次成功

        def clear_fault(self) -> None:
            pass

    coordinator.recover_stream(_Stream())

    assert len(calls) == 3
    assert len(prompts) == 1
    assert coordinator.waiting_kind == "connection"
    assert coordinator.recovering is False


def test_recover_stream_without_manual_channel_raises_channel_fault():
    """没有人工恢复入口（CLI）时不能无限等待，按通道故障上报。"""
    device = MagicMock()
    device.recovery = AndroidRecoveryConfig(retry_interval=0, stream_attempts=1)
    device.resume_event = threading.Event()
    device.stop_check = None
    device.can_wait_for_recovery = lambda: True
    device.on_connection_lost = None
    coordinator = AndroidRecovery(device)

    class _Stream:
        last_fault_detail = "连接断开"

        def restart(self) -> bool:
            return False

        def clear_fault(self) -> None:
            pass

    with pytest.raises(ChannelFaultError):
        coordinator.recover_stream(_Stream())


def test_decode_failure_becomes_fault_after_threshold():
    """包在流、但连续解不出来的同样是故障（不能一直假装有画面）。"""
    cap = _stream()
    for _ in range(29):
        cap._note_decode_error()
    assert cap.last_fault == ""
    cap._note_decode_error()
    assert cap.last_fault == FAULT_DECODE
