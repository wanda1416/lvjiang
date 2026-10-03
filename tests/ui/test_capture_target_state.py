"""执行目标变化与可选采集能力之间的状态契约。"""

from types import SimpleNamespace

from lvjiang.ui.main.execution_targets import (
    ExecutionTarget,
    ExecutionTargetRegistry,
    android_target_id,
)
from lvjiang.ui.main.window_ops import WindowOpsMixin


class _Preview:
    def clear(self) -> None:
        pass

    def setText(self, _text: str) -> None:  # noqa: N802 - Qt API shape
        pass


def test_active_target_refresh_notifies_optional_capture_panel() -> None:
    """连接与断开都要刷新采集按钮；通用层只调用可选钩子。"""
    target = SimpleNamespace(last_capture=None, ready=True)
    current = [target]
    refreshed = []
    host = SimpleNamespace(
        _active_execution_target=lambda: current[0],
        _capture_preview=lambda: None,
        _apply_rec_state=lambda: refreshed.append("refresh"),
        preview_label=_Preview(),
    )

    WindowOpsMixin._refresh_active_target_ui(host)
    current[0] = None
    WindowOpsMixin._refresh_active_target_ui(host)

    assert refreshed == ["refresh", "refresh"]


def test_switching_stream_backend_saves_recording_before_old_stream_stops(
    monkeypatch,
) -> None:
    """录屏依赖旧流；切换截图后端前必须先转正，不能留下空文件。"""
    events = []

    class _Capture:
        def __init__(self, name: str):
            self.name = name

        def start(self) -> bool:
            events.append(f"start:{self.name}")
            return True

        def stop(self) -> None:
            events.append(f"stop:{self.name}")

    target_id = android_target_id("A")
    target = ExecutionTarget(
        id=target_id,
        kind="adb",
        display_name="手机",
        capture=_Capture("old"),
        input_ctrl=object(),
        device=object(),
        serial="A",
        capture_method="scrcpy",
        streaming=True,
    )
    registry = ExecutionTargetRegistry()
    registry.put(target)
    new_capture = _Capture("new")
    monkeypatch.setattr(
        "lvjiang.core.android.create_capture_backend",
        lambda **_kwargs: new_capture,
    )
    host = SimpleNamespace(
        _execution_targets=registry,
        _running=False,
        _target_has_active_run=lambda _target_id: False,
        _screen_recorder=object(),
        _abort_screen_record=lambda reason: events.append(f"save:{reason}"),
        _sync_active_target_compat=lambda: events.append("sync"),
        _capture_preview=lambda: events.append("preview"),
        _apply_rec_state=lambda: events.append("refresh"),
        _refresh_execution_targets_ui=lambda: events.append("targets"),
        log_text=SimpleNamespace(append=lambda _message: None),
    )

    WindowOpsMixin._set_android_target_streaming(host, target_id, False)

    assert events.index("save:切换截图方式") < events.index("stop:old")
    assert target.capture is new_capture
    assert target.capture_method == "screencap"
    assert target.streaming is False
    assert "refresh" in events
