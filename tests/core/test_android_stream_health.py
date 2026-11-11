"""Android 画面流健康模型测试（简化版：仅 EOF + 用户停止）。

真实事故：看门狗的 stall/rx_idle 检测在正常静止画面上误报，
导致能正常截屏的任务被强制中断。现已删除全部主动探测，
只保留两个零误报信号：socket EOF 和用户主动停止。
"""
from __future__ import annotations

import struct
import threading
import time
from unittest.mock import MagicMock

import numpy as np
import pytest
from loguru import logger

from lvjiang.core.android.recovery import AndroidRecovery, ChannelFaultError
from lvjiang.core.android.scrcpy_capture import (
    FAULT_DECODE,
    FAULT_SOCKET_EOF,
    AndroidStreamCapture,
    _first_slice_nal_type,
    _OversizePacketError,
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
    cap._metrics_enabled = True
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


# ─── 帧时延观测 / 包边界 ──────────────────────────────────

def test_first_slice_nal_type_skips_sei_to_idr():
    """关键帧包可能先带 SEI/SPS，交叉校验必须找到真正的 slice NAL。"""
    payload = b"\x00\x00\x00\x01\x06\x05" + b"\x00\x00\x01\x65\x88\x84"
    assert _first_slice_nal_type(payload) == 5
    assert _first_slice_nal_type(b"\x00\x00\x01\x41\x9a") == 1
    assert _first_slice_nal_type(b"\x12\x34") is None


def test_pop_stream_packet_rejects_oversize_payload():
    """长度字段越界说明缓冲已失步，必须整包丢弃而不是从包里截断。"""
    header = struct.pack(">QI", 12345, 8 * 1024 * 1024)
    with pytest.raises(_OversizePacketError):
        AndroidStreamCapture._pop_stream_packet(header)


def test_lag_uses_pts_and_wall_delta():
    """滞后 = 本机时间增量 - PTS 增量，不需要设备时钟对齐。"""
    cap = _stream()
    assert cap._observe_media_packet(1_000_000, b"", 100.0) is None
    lag = cap._observe_media_packet(3_000_000, b"", 102.5)
    assert lag == pytest.approx(0.5)
    assert cap.lag_seconds is None  # 还没有解码帧

    cap._record_decoded_frame(3_000_000, 102.5)
    assert cap.lag_seconds == pytest.approx(0.5)
    # 基线尚未建立时，可行动滞后等于原始滞后
    assert cap.lag_excess_seconds == pytest.approx(0.5)


def test_pts_backwards_jump_rebuilds_anchor():
    """PTS 大幅回退（编码器换基准）不能算成负滞后。"""
    cap = _stream()
    cap._observe_media_packet(10_000_000, b"", 100.0)
    assert cap._observe_media_packet(1_000_000, b"", 100.1) is None
    assert cap._observe_media_packet(3_000_000, b"", 102.6) == pytest.approx(0.5)


def test_keyframe_flag_confirmed_by_idr_nal():
    """bit61 与 IDR NAL 同步出现后才标记关键帧位已确认。"""
    cap = _stream()
    cap._observe_media_packet(
        (1 << 61) | 5_000_000, b"\x00\x00\x00\x01\x65\x88", 100.0)
    assert cap._key_flag_state == 1
    assert cap._idr_seen == 1
    assert cap._idr_with_flag == 1


def test_lag_baseline_freezes_at_low_percentile():
    """基线取低分位而不是均值/中位数，避免把少量积压算进正常延迟。"""
    cap = _stream()
    with cap._metrics_lock:
        cap._lag_samples.extend([0.2] * 135 + [5.0] * 15)
        now = cap._metrics_at + 1.0
    cap._maybe_log_metrics(now)
    assert cap._lag_baseline == pytest.approx(0.2)


def test_frame_to_bgr_returns_owned_array():
    """发布出去的帧必须拥有自己的缓冲，不能是 AVFrame 的视图。"""
    import av

    cap = _stream()
    frame = av.VideoFrame(4, 4, format="yuv420p")
    bgr = cap._frame_to_bgr(frame)
    assert bgr.flags["C_CONTIGUOUS"]
    assert bgr.flags["OWNDATA"]


def test_baseline_samples_wait_for_stable_window():
    """帧率稳定前不攒基线，避免构建期积压被冻进基线。"""
    cap = _stream()
    cap._observe_media_packet(1_000_000, b"", 100.0)
    cap._record_decoded_frame(2_000_000, 101.0)
    assert cap._lag_samples == []

    cap._baseline_window_open = True
    cap._record_decoded_frame(3_000_000, 102.0)
    assert len(cap._lag_samples) == 1


def test_oversize_packet_declares_desync_fault():
    """声明长度越界后剩余字节仍在 socket 里，必须声明故障重建会话。"""
    class _OversizeSocket:
        def __init__(self):
            self._sent = False

        def recv(self, _n):
            if self._sent:
                return b""
            self._sent = True
            return struct.pack(">QI", 0, 8 * 1024 * 1024)

        def close(self):
            pass

    cap = _stream()
    cap._sock = _OversizeSocket()
    cap._decode_loop()
    assert cap.last_fault == FAULT_DECODE


def test_metrics_summary_is_info_level():
    """压测只看 info 时也要能看到滞后 p50/p90/max。"""
    cap = _stream()
    now = time.monotonic()
    with cap._metrics_lock:
        cap._metrics_at = now - 1.0
        cap._metrics_summary_at = now - 11.0
        cap._lag_interval_samples.extend([0.1, 0.2, 0.3])

    records: list[str] = []
    sink = logger.add(lambda message: records.append(str(message)), level="INFO")
    try:
        cap._maybe_log_metrics(now)
    finally:
        logger.remove(sink)
    assert any("压测摘要" in message for message in records)


def test_metrics_summary_is_silent_when_disabled():
    """正常任务默认关闭观测，不应输出压测摘要。"""
    cap = _stream()
    cap._metrics_enabled = False
    now = time.monotonic()
    with cap._metrics_lock:
        cap._metrics_at = now - 1.0
        cap._metrics_summary_at = now - 11.0
        cap._lag_interval_samples.extend([0.1, 0.2, 0.3])

    records: list[str] = []
    sink = logger.add(lambda message: records.append(str(message)), level="INFO")
    try:
        cap._maybe_log_metrics(now)
    finally:
        logger.remove(sink)
    assert not any("压测摘要" in message for message in records)


def test_metrics_disabled_skips_lag_sampling():
    """关闭观测时不累积滞后样本，避免长跑内存增长。"""
    cap = _stream()
    cap._metrics_enabled = False
    cap._baseline_window_open = True
    cap._record_decoded_frame(2_000_000, 101.0)
    assert cap._lag_samples == []
    assert cap._lag_interval_samples == []
