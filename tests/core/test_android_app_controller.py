import struct

import numpy as np
import pytest

from lvjiang.core.android.app_controller import AndroidAppController, AndroidAppError
from lvjiang.core.config import AndroidAppConfig


class FakeDevice:
    def __init__(self):
        self.running = True
        self.calls = []

    def shell(self, *args, timeout=15):
        self.calls.append(args)
        if args[:2] == ("am", "force-stop"):
            self.running = False
            return ""
        if args[0] in {"monkey", "am"}:
            self.running = True
            return "Events injected: 1"
        if args[0] == "pidof":
            return "1234" if self.running else ""
        return ""


def _controller(device=None, capture=None):
    return AndroidAppController(
        device or FakeDevice(),
        {"game": AndroidAppConfig(
            package="com.example.game", orientation="landscape")},
        capture=capture,
    )


def test_stop_waits_until_process_disappears():
    device = FakeDevice()
    assert _controller(device).stop("game") is True
    assert ("am", "force-stop", "com.example.game") in device.calls
    assert not device.running


def test_start_uses_launcher_when_activity_is_empty():
    device = FakeDevice()
    device.running = False
    assert _controller(device).start("game") is True
    assert any(call[0] == "monkey" and "com.example.game" in call
               for call in device.calls)


def test_unknown_app_reports_configuration_entry():
    with pytest.raises(AndroidAppError, match="应用注册"):
        _controller().stop("missing")


class StableCapture:
    def wait_ready(self, timeout, expected_orientation="any"):
        assert expected_orientation == "landscape"
        return True

    def capture(self, timeout=5):
        return np.zeros((90, 160, 3), dtype=np.uint8)


def test_wait_stable_frame_checks_orientation_and_stability():
    assert _controller(capture=StableCapture()).wait_stable_frame(
        "game", timeout=1, stable_duration=0.01, interval=0.01) is True


def _session(width, height):
    return bytes([0x80, 0, 0, 0]) + struct.pack(">II", width, height)


def _media(payload):
    return bytes(8) + struct.pack(">I", len(payload)) + payload


def test_scrcpy_packet_parser_keeps_alignment_across_session_changes():
    from lvjiang.core.android.scrcpy_capture import AndroidStreamCapture

    stream = _media(b"first") + _session(1080, 2400) + _media(b"second")
    first = AndroidStreamCapture._pop_stream_packet(stream)
    assert first is not None and first[0] == "media" and first[2] == b"first"
    portrait = AndroidStreamCapture._pop_stream_packet(first[3])
    assert portrait is not None and portrait[0] == "session" and portrait[2] == b""
    second = AndroidStreamCapture._pop_stream_packet(portrait[3])
    assert second is not None and second[0] == "media" and second[2] == b"second"
    assert second[3] == b""


def test_android_app_statements_parse_literal_and_variable():
    from lvjiang.workflows.grammar import AndroidAppAction, parse_text

    program = parse_text(
        'app stop "game" timeout 15\napp start $target timeout $launch_timeout\n')

    stop, start = program.body
    assert isinstance(stop, AndroidAppAction)
    assert stop.action == "stop" and stop.name.value == "game"
    assert stop.timeout.value == 15
    assert isinstance(start, AndroidAppAction)
    assert start.action == "start" and start.name.name == "target"
    assert start.timeout.name == "launch_timeout"


def test_android_app_statement_delegates_to_shared_controller():
    from lvjiang.workflows.grammar import parse_text
    from tests.workflows.conftest import make_engine

    calls = []
    controller = type("Controller", (), {
        "stop": lambda self, name, timeout: calls.append(("stop", name, timeout)),
    })()
    engine = make_engine()
    engine._app_controller = controller

    engine._exec_stmt(parse_text('app stop "game" timeout 12\n').body[0])

    assert calls == [("stop", "game", 12.0)]


def test_app_is_running_dispatches_by_registered_target_not_env(monkeypatch):
    from lvjiang.core.app_controller import AppController

    apps = {
        "phone": AndroidAppConfig(package="com.example.game"),
        "desktop": AndroidAppConfig(
            platform="pc", executable=r"C:\\Game\\game.exe"),
    }
    controller = AppController(apps, device=FakeDevice())
    monkeypatch.setattr(
        controller._windows, "is_running", lambda name: name == "desktop")

    assert controller.is_running("phone") is True
    assert controller.is_running("desktop") is True


def test_scrcpy_packet_parser_waits_for_incomplete_packets():
    """半包不得消费：header 不足 12 字节、或 payload 未到齐都返回 None。"""
    from lvjiang.core.android.scrcpy_capture import AndroidStreamCapture

    assert AndroidStreamCapture._pop_stream_packet(b"") is None
    assert AndroidStreamCapture._pop_stream_packet(bytes(11)) is None

    packet = _media(b"payload")
    assert AndroidStreamCapture._pop_stream_packet(packet[:-1]) is None
    # 补齐最后一个字节后应能完整弹出，证明前面没有丢字节。
    parsed = AndroidStreamCapture._pop_stream_packet(packet)
    assert parsed is not None and parsed[2] == b"payload" and parsed[3] == b""


def test_scrcpy_session_packet_resets_readiness_and_target_size():
    """session 包只带尺寸：必须清空旧帧并把后续帧对齐到新尺寸。"""
    from unittest.mock import MagicMock

    from lvjiang.core.android.scrcpy_capture import AndroidStreamCapture

    stream = AndroidStreamCapture(MagicMock())
    stream._latest_frame = np.zeros((10, 20, 3), dtype=np.uint8)
    stream._ready_event.set()
    stream._transitioning = False
    stream._codec_config = b"stale"

    stream._handle_session_packet(_session(1080, 2400), initial=True)

    assert stream._session_size == (1080, 2400)
    assert stream._latest_frame is None
    assert stream._codec_config == b""
    assert stream._pending_session is True
    assert stream.is_transitioning is True
    assert stream._ready_event.is_set() is False


class FakeSocket:
    """按字节喂数据的假 socket，只实现 _recv_exact 用到的 recv。"""

    def __init__(self, payload: bytes):
        self._payload = payload

    def recv(self, size: int) -> bytes:
        chunk, self._payload = self._payload[:size], self._payload[size:]
        return chunk


def _handshake(session: bytes) -> bytes:
    """dummy byte + 64 字节设备名 + 4 字节 codec id + 12 字节首包。"""
    return b"\x00" + b"device".ljust(64, b"\x00") + b"h264" + session


def test_protocol_header_rejects_unexpected_first_packet_without_retrying():
    """首包标志位不符只可能是 server 版本变了，必须抛错而不是返回 False。

    返回 False 会被 start() 当成「server 还没就绪」重试三次，最后只报一句
    通用的读取失败，用户无从判断是版本问题。
    """
    from unittest.mock import MagicMock

    from lvjiang.core.android.scrcpy_capture import (
        AndroidStreamCapture,
        ScrcpyProtocolError,
    )

    stream = AndroidStreamCapture(MagicMock())
    # 高位未置 1：本客户端不认识的包结构
    stream._sock = FakeSocket(_handshake(bytes(4) + struct.pack(">II", 1080, 2400)))

    with pytest.raises(ScrcpyProtocolError) as exc_info:
        stream._read_protocol_header()

    assert "0x00" in str(exc_info.value)


def test_protocol_header_accepts_expected_session_packet():
    from unittest.mock import MagicMock

    from lvjiang.core.android.scrcpy_capture import AndroidStreamCapture

    stream = AndroidStreamCapture(MagicMock())
    stream._sock = FakeSocket(_handshake(_session(1080, 2400)))

    assert stream._read_protocol_header() is True
    assert stream._session_size == (1080, 2400)


def test_wait_ready_checks_stop_between_short_waits():
    from types import SimpleNamespace

    waits = []
    def wait_ready(timeout, *, expected_orientation):
        assert expected_orientation == "landscape"
        waits.append(timeout)
        return False
    controller = _controller(capture=SimpleNamespace(wait_ready=wait_ready))
    controller.stop_check = lambda: bool(waits)
    with pytest.raises(AndroidAppError, match="用户已停止"):
        controller.wait_stable_frame("game", timeout=60)
    assert len(waits) == 1
    assert 0 < waits[0] <= 0.2


def test_wait_ready_already_stopped_never_waits():
    from unittest.mock import Mock

    capture = Mock()
    controller = _controller(capture=capture)
    controller.stop_check = lambda: True
    with pytest.raises(AndroidAppError, match="用户已停止"):
        controller.wait_stable_frame("game")
    capture.wait_ready.assert_not_called()
