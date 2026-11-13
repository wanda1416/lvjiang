"""设备执行占用必须在启动前探测，而不是让工作流线程抛异常。

手机端正在跑别的任务时，`engine.execute → control_begin` 会失败。这条冲突如果
留到后台线程才暴露，用户只会看到"一闪就结束 + 一行 ERROR 日志"。预检用同一
个调用判据（control_begin 拿到即释放），所以不会出现"预检放行、真跑被拒"。
"""
from __future__ import annotations

from lvjiang.core.android.agent import AgentOpError
from lvjiang.ui.main.execution_targets import (
    ExecutionTarget,
    ExecutionTargetRegistry,
    android_target_id,
)
from lvjiang.ui.main.run_control import RunControlMixin


class _BusyInput:
    def begin_execution(self) -> None:
        raise AgentOpError("设备正在执行其他任务")

    def end_execution(self) -> None:
        pass


class _FreeInput:
    def __init__(self) -> None:
        self.released = 0

    def begin_execution(self) -> None:
        pass

    def end_execution(self) -> None:
        self.released += 1


def _host(input_ctrl) -> RunControlMixin:
    registry = ExecutionTargetRegistry()
    registry.put(ExecutionTarget(
        id=android_target_id("A"), kind="adb", display_name="设备 A",
        capture=object(), input_ctrl=input_ctrl, device=object(),
    ))
    host = type("Host", (RunControlMixin,), {})()
    host._execution_targets = registry
    return host


def test_busy_device_is_reported_before_start() -> None:
    """设备端拒绝交出占用时，预检要返回设备给的原因。"""
    host = _host(_BusyInput())

    assert host.device_execution_busy() == "设备正在执行其他任务"


def test_free_device_passes_precheck_and_releases() -> None:
    """空闲设备放行，并且预检拿到的占用必须立刻释放。"""
    input_ctrl = _FreeInput()
    host = _host(input_ctrl)

    assert host.device_execution_busy() == ""
    assert input_ctrl.released == 1


def test_non_agent_backend_never_blocks_start() -> None:
    """没有代理能力的后端（窗口、纯 ADB 输入）直接放行，交给原流程。"""
    host = _host(object())

    assert host.device_execution_busy() == ""
