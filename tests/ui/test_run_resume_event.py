"""ADB 断连等待必须按运行实例唤醒，不能跨目标串台。

主窗口只有一个 `_adb_resume_event` 兼容字段，而等待重连的任务可以有多个。
唤醒错了对象，那个任务会以为连接已恢复，继续去打已经死掉的 transport。
"""
from __future__ import annotations

import threading

from lvjiang.ui.main.execution_runs import (
    ExecutionRunManager,
)
from lvjiang.ui.main.execution_targets import (
    WINDOW_TARGET_ID,
    ExecutionTarget,
    ExecutionTargetRegistry,
    android_target_id,
)
from lvjiang.ui.main.run_control import RunControlMixin


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
        resume_event=threading.Event(),
    )
    registry.put(window)
    registry.put(device)
    return registry, window, device


def _host(registry, manager, *, current=None):
    host = type("Host", (RunControlMixin,), {})()
    host._run_manager = manager
    host._execution_targets = registry
    host._current_run_context = current
    return host


def test_resume_event_comes_from_the_run_not_the_viewed_target() -> None:
    """停止/恢复唤醒的必须是该运行实例自己的等待。

    `_adb_resume_event` 只在被选中目标自带 resume_event 时才被覆盖，窗口目标
    没有这个字段。修复前切到窗口目标后它仍指向设备 A，停止窗口任务会把 A 上
    正在等待重连的任务一起唤醒，让它继续去打已经死掉的连接。
    """
    registry, window, device = _registry()
    manager = ExecutionRunManager(lv1_check=lambda: True, parallel_enabled=True)
    _, device_run = manager.try_begin(
        target=device.snapshot(), username="甲", name="设备任务")
    _, window_run = manager.try_begin(
        target=window.snapshot(), username="乙", name="窗口任务")
    assert device_run is not None and window_run is not None
    host = _host(registry, manager, current=window_run)
    # 兼容字段残留着上一台设备的事件，正是旧实现会误唤醒的那一个
    host._adb_resume_event = device.resume_event

    resolved = host._run_resume_event(window_run)

    assert resolved is not device.resume_event
    assert resolved is window_run.target_snapshot.resume_event
    assert host._run_resume_event(device_run) is device.resume_event


def test_resume_event_falls_back_to_the_compat_field_without_a_run() -> None:
    """没有运行实例时（独立测试宿主、工具入口）仍用主窗口那一个。"""
    registry, _window, _device = _registry()
    manager = ExecutionRunManager(lv1_check=lambda: True, parallel_enabled=True)
    host = _host(registry, manager)
    fallback = threading.Event()
    host._adb_resume_event = fallback

    assert host._run_resume_event(None) is fallback
