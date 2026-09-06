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
    with pytest.raises(AndroidAppError, match="安卓设置"):
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
    engine._android_device = object()
    engine._android_app_controller = controller

    engine._exec_stmt(parse_text('app stop "game" timeout 12\n').body[0])

    assert calls == [("stop", "game", 12.0)]
