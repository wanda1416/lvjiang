"""托盘只有一个物理实例，图标必须表示全局而不是当前查看目标。

设备 A 在跑、UI 停在空闲的窗口目标上时，图标跟着当前目标显示绿色空闲，会
让用户以为没有任务在执行，可以直接关程序。
"""
from __future__ import annotations

from lvjiang.ui.main.execution_runs import (
    ExecutionRunManager,
    RunState,
)
from lvjiang.ui.main.execution_targets import (
    WINDOW_TARGET_ID,
    ExecutionTarget,
    ExecutionTargetRegistry,
    android_target_id,
)
from lvjiang.ui.main.run_control import RunControlMixin
from lvjiang.ui.main.tray_ops import TrayOpsMixin


def _registry() -> tuple[ExecutionTargetRegistry, ExecutionTarget, ExecutionTarget]:
    registry = ExecutionTargetRegistry()
    window = ExecutionTarget(
        id=WINDOW_TARGET_ID, kind="windows", display_name="游戏窗口",
        capture=object(), input_ctrl=object(),
        window={"hwnd": 1, "left": 0, "top": 0},
    )
    device = ExecutionTarget(
        id=android_target_id("A"), kind="adb", display_name="设备 A",
        capture=object(), input_ctrl=object(), device=object(),
    )
    registry.put(window)
    registry.put(device)
    return registry, window, device


def _host(registry, manager, *, current=None):
    host = type("Host", (RunControlMixin, TrayOpsMixin), {})()
    host._run_manager = manager
    host._execution_targets = registry
    host._current_run_context = current
    return host


def test_tray_icon_uses_the_most_severe_state_across_all_targets() -> None:
    """托盘图标只有一个，必须表示全局，而不是当前查看的那一个目标。

    否则「设备 A 在跑、UI 停在空闲的窗口目标上」会显示绿色空闲图标，用户会
    以为没有任务在执行。
    """
    registry, window, device = _registry()
    manager = ExecutionRunManager(lv1_check=lambda: True, parallel_enabled=True)
    _, run = manager.try_begin(
        target=device.snapshot(), username="甲", name="设备任务")
    assert run is not None
    host = _host(registry, manager)

    # 启动中也算「有任务在跑」：线程活着、目标被占用
    assert TrayOpsMixin._tray_icon_state(host, "idle") == "running"

    manager.set_state(run.task_run_id, RunState.RUNNING)
    assert TrayOpsMixin._tray_icon_state(host, "idle") == "running"

    manager.set_state(run.task_run_id, RunState.PAUSED)
    assert TrayOpsMixin._tray_icon_state(host, "idle") == "paused"

    manager.set_state(run.task_run_id, RunState.WAITING_TARGET)
    assert TrayOpsMixin._tray_icon_state(host, "idle") == "running"

    manager.finish(run.task_run_id, RunState.COMPLETED)
    assert TrayOpsMixin._tray_icon_state(host, "not_ready") == "idle"
    assert TrayOpsMixin._aggregate_tray_state(host, "not_ready") == "not_ready"
