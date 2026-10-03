"""主窗口连接目标注册表。

连接和执行是两个正交状态：注册表可以同时保留一个窗口与多台 Android
设备，``active_target_id`` 只表示下一次自动化使用哪一个目标。
"""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from typing import Any, Literal

TargetKind = Literal["windows", "adb"]
WINDOW_TARGET_ID = "window"


def android_target_id(device_identity: str) -> str:
    """从逻辑设备身份生成不暴露原始设备标识的稳定目标 ID。"""
    digest = hashlib.sha256(str(device_identity).encode("utf-8")).hexdigest()
    return f"android:{digest[:24]}"


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


@dataclass
class LaunchDraft:
    """某个执行目标在当前连接会话中的待运行编辑态。"""

    username: str | None = None
    workflow_id: str = ""
    environment: str = ""
    layout: str = ""
    plan_id: str = ""
    reference_space: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResourceBinding:
    generation: int
    capture: Any
    input_ctrl: Any
    device: Any
    window: dict[str, Any] | None
    resume_event: Any


class TargetHandle:
    """任务持有的稳定 I/O 句柄，重连只替换内部绑定。"""

    def __init__(self, target: "ExecutionTarget") -> None:
        self._lock = threading.RLock()
        self._binding = ResourceBinding(
            1, target.capture, target.input_ctrl, target.device,
            dict(target.window) if target.window is not None else None,
            target.resume_event,
        )

    def binding(self) -> ResourceBinding:
        with self._lock:
            return self._binding

    def rebind(self, target: "ExecutionTarget") -> ResourceBinding:
        with self._lock:
            self._binding = ResourceBinding(
                self._binding.generation + 1,
                target.capture, target.input_ctrl, target.device,
                dict(target.window) if target.window is not None else None,
                target.resume_event,
            )
            return self._binding

    def update_window(self, window: dict[str, Any]) -> ResourceBinding:
        """更新同一窗口目标的几何信息，不改变资源代际。"""
        with self._lock:
            current = dict(self._binding.window or {})
            current.update(window)
            self._binding = ResourceBinding(
                self._binding.generation,
                self._binding.capture,
                self._binding.input_ctrl,
                self._binding.device,
                current,
                self._binding.resume_event,
            )
            return self._binding


class _ResourceProxy:
    """在每次属性/方法访问边界解析当前资源。"""

    def __init__(self, handle: TargetHandle, field_name: str) -> None:
        object.__setattr__(self, "_handle", handle)
        object.__setattr__(self, "_field_name", field_name)

    def _resource(self):
        resource = getattr(
            object.__getattribute__(self, "_handle").binding(),
            object.__getattribute__(self, "_field_name"),
        )
        if resource is None:
            raise RuntimeError("执行目标当前没有可用资源")
        return resource

    def __getattr__(self, name: str):
        return getattr(self._resource(), name)

    def __setattr__(self, name: str, value) -> None:
        setattr(self._resource(), name, value)


@dataclass(frozen=True)
class ExecutionTargetSnapshot:
    """一次自动化冻结的目标资源；UI 后续状态变化不能替换这些引用。"""

    id: str
    kind: TargetKind
    display_name: str
    capture: Any
    input_ctrl: Any
    input_kind: str
    window: dict[str, Any] | None
    device: Any
    resume_event: Any
    handle: TargetHandle


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
    launch_draft: LaunchDraft | None = None
    handle: TargetHandle | None = None

    @property
    def ready(self) -> bool:
        return self.status == "connected" and self.capture is not None \
            and self.input_ctrl is not None

    def snapshot(self) -> ExecutionTargetSnapshot:
        handle = self.handle or TargetHandle(self)
        self.handle = handle
        binding = handle.binding()
        return ExecutionTargetSnapshot(
            id=self.id,
            kind=self.kind,
            display_name=self.display_name,
            capture=_ResourceProxy(handle, "capture"),
            input_ctrl=_ResourceProxy(handle, "input_ctrl"),
            input_kind=self.input_kind,
            window=dict(self.window) if self.window is not None else None,
            device=_ResourceProxy(handle, "device") if binding.device is not None else None,
            resume_event=binding.resume_event,
            handle=handle,
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
        if old is not None and old.handle is not None:
            target.handle = old.handle
            target.handle.rebind(target)
        elif target.handle is None:
            target.handle = TargetHandle(target)
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
        """按当前 transport serial 查找已连接目标。"""
        return next(
            (target for target in self._targets.values()
             if target.kind == "adb" and target.serial == serial),
            None,
        )
