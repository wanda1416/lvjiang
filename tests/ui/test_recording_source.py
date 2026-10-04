"""录屏来源在开始录制时冻结，不跟随当前观察目标。

录屏属于观察面，但帧来自某一个具体目标。来源跟着预览走的话，切换观察目标
会把两台设备的画面静默拼进同一个视频；而换绑截图后端或断开连接确实会让帧
流中断，那时才需要先把已录内容转正保存。
"""
from __future__ import annotations

import numpy as np

from lvjiang.ui.main.execution_targets import (
    ExecutionTarget,
    ExecutionTargetRegistry,
    android_target_id,
)
from lvjiang.ui.main.window_ops import WindowOpsMixin


class _Backend:
    """可区分身份、能记录是否被停掉的假后端。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.stopped = False

    def start(self) -> bool:
        return True

    def stop(self) -> None:
        self.stopped = True


class _Log:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def append(self, text: str) -> None:
        self.lines.append(text)


class _Host(WindowOpsMixin):
    """只提供被测方法真正用到的协作者，其余靠 getattr 守卫自然跳过。"""

    def __init__(self, registry: ExecutionTargetRegistry) -> None:
        self._execution_targets = registry
        self.log_text = _Log()
        self._screen_recorder = None
        self._record_target_id = None
        # 预览信号在真实窗口里是 pyqtSignal；这里只要能被 emit 调用即可
        self._scrcpy_frame_ready = type(
            "Signal", (), {"emit": lambda self, *args: None})()

    def _target_has_active_run(self, target_id: str) -> bool:
        return False

    def _sync_active_target_compat(self) -> None:
        pass

    def _refresh_execution_targets_ui(self) -> None:
        pass

    def _capture_preview(self) -> None:
        pass


def test_recording_source_is_frozen_at_start_not_the_viewed_target() -> None:
    """录屏只收冻结来源的帧；切到别的目标看预览不改变视频内容。"""
    registry = ExecutionTargetRegistry()
    recorded = ExecutionTarget(
        id=android_target_id("A"), kind="adb", display_name="设备 A",
        capture=_Backend("scrcpy"), input_ctrl=_Backend("adb"))
    other = ExecutionTarget(
        id=android_target_id("B"), kind="adb", display_name="设备 B",
        capture=_Backend("scrcpy"), input_ctrl=_Backend("adb"))
    registry.put(recorded)
    registry.put(other)
    pushed: list = []
    host = _Host(registry)
    host._screen_recorder = type("Rec", (), {"push": pushed.append})()
    host._record_target_id = recorded.id
    # 用户正在看 B 的预览，录的却是 A
    registry.select(other.id)

    frame_a = np.zeros((2, 2, 3), dtype=np.uint8)
    frame_b = np.ones((2, 2, 3), dtype=np.uint8)
    host._on_scrcpy_frame(recorded.id, frame_a)
    host._on_scrcpy_frame(other.id, frame_b)

    assert pushed == [frame_a], "只有录制来源的帧能进视频"


def test_only_the_recording_target_aborts_the_recording() -> None:
    """动别的目标不牵连正在录的那一路；动来源本身仍然先安全收尾。"""
    registry = ExecutionTargetRegistry()
    host = _Host(registry)
    host._screen_recorder = object()
    host._record_target_id = "android:A"
    aborted: list[str] = []
    host._abort_screen_record = aborted.append  # type: ignore[method-assign]

    host._abort_recording_for_target("android:B", "切换截图方式")
    assert aborted == []

    host._abort_recording_for_target("android:A", "切换截图方式")
    assert aborted == ["切换截图方式"]
