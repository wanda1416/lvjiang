import json
import zlib
from pathlib import Path

import pytest

from lvjiang.core import input_trace as trace_module
from lvjiang.core.input_trace import (
    TRACE_MAGIC,
    TRACE_VERSION,
    InputTrace,
    InputTraceError,
    InputTraceEvent,
    decode_input_trace,
    encode_input_trace,
    save_input_trace_bundle,
)


def _trace() -> InputTrace:
    return InputTrace(
        source_width=1000,
        source_height=800,
        events=(
            InputTraceEvent(0, "move", (4, -2)),
            InputTraceEvent(1200, "button", ("left", True)),
            InputTraceEvent(2300, "button", ("left", False)),
            InputTraceEvent(3000, "wheel", (-120,)),
            InputTraceEvent(4000, "key", ("W", True)),
            InputTraceEvent(9000, "key", ("W", False)),
        ),
    )


def test_lvtrace_round_trip_is_deterministic():
    encoded = encode_input_trace(_trace())
    assert encode_input_trace(_trace()) == encoded
    assert decode_input_trace(encoded) == _trace()


def test_lvtrace_accepts_side_button_events():
    """侧键（前进/后退）是合法鼠标键名，round-trip 不应报错。"""
    trace = InputTrace(
        1000, 800,
        (
            InputTraceEvent(0, "button", ("x1", True)),
            InputTraceEvent(1000, "button", ("x1", False)),
            InputTraceEvent(2000, "button", ("x2", True)),
            InputTraceEvent(3000, "button", ("x2", False)),
        ),
    )
    assert decode_input_trace(encode_input_trace(trace)) == trace


def test_lvtrace_rejects_unknown_button_name():
    bad_trace = InputTrace(
        1000, 800, (InputTraceEvent(0, "button", ("mouse6", True)),))
    with pytest.raises(InputTraceError, match="鼠标键事件不合法"):
        encode_input_trace(bad_trace)


def test_lvtrace_rejects_corrupt_data():
    with pytest.raises(InputTraceError, match="有效的 lvtrace"):
        decode_input_trace(b"not-a-trace")


def test_lvtrace_rejects_unknown_key_name_on_encode():
    """未知按键名必须在编码时就报错，不能拖到回放中途才炸。"""
    bad_trace = InputTrace(
        1000, 800, (InputTraceEvent(0, "key", ("FOOBAR", True)),))
    with pytest.raises(InputTraceError, match="按键名未知"):
        encode_input_trace(bad_trace)


def test_lvtrace_rejects_unknown_key_name_on_decode():
    """手改/版本漂移的 .lvtrace（绕过 encode_input_trace 自身校验）
    解码时同样要拒绝未知按键名，而不是等 normalize_key 在回放中途炸。
    """
    payload = {
        "source": [1000, 800],
        "events": [[0, "key", "FOOBAR", True]],
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    hand_crafted = TRACE_MAGIC + bytes([TRACE_VERSION]) + zlib.compress(raw, level=9)

    with pytest.raises(InputTraceError, match="按键名未知"):
        decode_input_trace(hand_crafted)


def test_bundle_save_creates_central_trace_and_relative_reference(tmp_path):
    workflows = tmp_path / "workflows"
    wf_path = workflows / "standalone" / "recorded.wf"

    saved_wf, saved_trace, text = save_input_trace_bundle(
        wf_path,
        'replay input_trace "__LVTRACE__"',
        _trace(),
        workflows_root=workflows,
    )

    assert saved_wf == wf_path.resolve()
    assert saved_trace.parent == (workflows / "lvtrace").resolve()
    assert saved_trace.suffix == ".lvtrace"
    assert f'"../lvtrace/{saved_trace.name}"' in text
    assert saved_wf.read_text(encoding="utf-8") == text
    assert decode_input_trace(saved_trace.read_bytes()) == _trace()


def test_bundle_failure_never_publishes_wf_with_missing_trace(tmp_path, monkeypatch):
    workflows = tmp_path / "workflows"
    wf_path = workflows / "recorded.wf"
    original_atomic_write = trace_module._atomic_write
    calls = 0

    def fail_wf_write(path: Path, data: bytes):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk full")
        original_atomic_write(path, data)

    monkeypatch.setattr(trace_module, "_atomic_write", fail_wf_write)
    with pytest.raises(OSError, match="disk full"):
        save_input_trace_bundle(
            wf_path,
            'replay input_trace "__LVTRACE__"',
            _trace(),
            workflows_root=workflows,
        )

    assert not wf_path.exists()
    assert list((workflows / "lvtrace").glob("*.lvtrace")) == []
