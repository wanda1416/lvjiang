"""主窗口连接目标注册表。

连接和执行是两个正交状态：注册表可以同时保留一个窗口与多台 Android
设备，``active_target_id`` 只表示下一次自动化使用哪一个目标。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

TargetKind = Literal["windows", "adb"]
WINDOW_TARGET_ID = "window"


def android_target_id(serial: str) -> str:
    """返回稳定的 Android 目标 ID。"""
    return f"android:{serial}"


@dataclass
class WindowConnectionDraft:
    """下一次定位窗口采用的连接参数。"""

    background_input: bool = False
    background_capture: bool = False


@dataclass
class AndroidConnectionDraft:
    """下一次连接 Android 设备采用的连接参数。"""

    capture_method: str = "screencap"
    device_execution: bool = False


@dataclass(frozen=True)
class ExecutionTargetSnapshot:
    """一次自动化冻结的目标资源；UI 后续状态变化不能替换这些引用。"""

    id: str
    kind: TargetKind
    capture: Any
    input_ctrl: Any
    input_kind: str
    window: dict[str, Any] | None
    device: Any
    resume_event: Any


@dataclass
class ExecutionTarget:
    """一个已经建立、可供执行选择的目标。"""

    id: str
    kind: TargetKind
    display_name: str
    status: str = "connected"
    capture: Any = None
    input_ctrl: Any = None
    input_kind: str = ""
    window: dict[str, Any] | None = None
    device: Any = None
    agent: Any = None
    serial: str = ""
    width: int = 0
    height: int = 0
    capture_method: str = ""
    streaming: bool = False
    resume_event: Any = None
    connection_bridge: Any = None
    last_capture: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return self.status == "connected" and self.capture is not None \
            and self.input_ctrl is not None

    def snapshot(self) -> ExecutionTargetSnapshot:
        return ExecutionTargetSnapshot(
            id=self.id,
            kind=self.kind,
            capture=self.capture,
            input_ctrl=self.input_ctrl,
            input_kind=self.input_kind,
            window=dict(self.window) if self.window is not None else None,
            device=self.device,
            resume_event=self.resume_event,
        )


class ExecutionTargetRegistry:
    """保存一个窗口目标和任意数量的 Android 目标。"""

    def __init__(self) -> None:
        self._targets: dict[str, ExecutionTarget] = {}
        self.active_target_id: str | None = None

    def all(self) -> list[ExecutionTarget]:
        window = self._targets.get(WINDOW_TARGET_ID)
        devices = sorted(
            (target for target in self._targets.values()
             if target.kind == "adb"),
            key=lambda target: (target.display_name.casefold(), target.id),
        )
        return ([window] if window is not None else []) + devices

    def get(self, target_id: str | None) -> ExecutionTarget | None:
        if not target_id:
            return None
        return self._targets.get(target_id)

    def active(self) -> ExecutionTarget | None:
        return self.get(self.active_target_id)

    def put(self, target: ExecutionTarget) -> ExecutionTarget | None:
        """登记目标，返回同 ID 的旧目标；首个目标自动成为执行目标。"""
        if target.kind == "windows" and target.id != WINDOW_TARGET_ID:
            raise ValueError("窗口目标必须使用固定 ID")
        old = self._targets.get(target.id)
        self._targets[target.id] = target
        if self.active_target_id is None:
            self.active_target_id = target.id
        return old

    def select(self, target_id: str) -> ExecutionTarget:
        target = self._targets.get(target_id)
        if target is None:
            raise KeyError(target_id)
        self.active_target_id = target_id
        return target

    def remove(self, target_id: str) -> ExecutionTarget | None:
        removed = self._targets.pop(target_id, None)
        if self.active_target_id == target_id:
            remaining = self.all()
            self.active_target_id = remaining[0].id if remaining else None
        return removed

    def device(self, serial: str) -> ExecutionTarget | None:
        return self.get(android_target_id(serial))
