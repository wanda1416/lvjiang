"""并发启动门禁必须在点击之前就反映到按钮上。

RunManager 是能力真源。UI 不另算一套布尔条件，而是按同一份 StartDecision
置灰并说明原因——否则用户对着绿色的「开始执行」点下去，才被告知要激活 Lv1。
"""
from __future__ import annotations

from lvjiang.ui.main.execution_runs import (
    ExecutionRunManager,
    StartDenial,
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


def test_lv1_denial_is_known_before_the_user_clicks() -> None:
    """并发门禁是能力真源，UI 必须按同一份判定展示，而不是点了才报。"""
    registry, window, device = _registry()
    entitled = False
    manager = ExecutionRunManager(
        lv1_check=lambda: entitled, parallel_enabled=True)
    host = _host(registry, manager)

    registry.select(device.id)
    assert host._start_denial() is None, "没有其他运行实例时不该拦"

    _, run = manager.try_begin(
        target=window.snapshot(), username="甲", name="窗口任务")
    assert run is not None
    registry.select(device.id)

    denial = host._start_denial()
    assert denial is not None
    assert denial.denial_code == StartDenial.LV1_REQUIRED
    assert "Lv1" in denial.reason

    entitled = True
    assert host._start_denial() is None, "授权在每次启动时重新读取"


def test_denied_label_is_defined_once_for_every_run_button() -> None:
    """主页面、批量页和调律页的运行按钮共用同一份文案来源。

    三个按钮都订阅同一个状态信号，各自拼一份就会漂移。
    """
    registry, window, device = _registry()
    entitled = False
    manager = ExecutionRunManager(
        lv1_check=lambda: entitled, parallel_enabled=True)
    host = _host(registry, manager)
    registry.select(device.id)

    assert host.start_denied_label() == ""

    manager.try_begin(target=window.snapshot(), username="甲", name="窗口任务")
    assert host.start_denied_label() == "需激活 Lv1"

    entitled = True
    assert host.start_denied_label() == ""


def test_start_denial_does_not_consume_the_user_lock_slot() -> None:
    """按钮状态只反映目标占用和并发授权，用户锁冲突仍留给启动时即时判定。

    用空用户名查询，否则「这个用户正忙」会把按钮也画成灰的——而换一个用户
    本来就能启动。
    """
    registry, window, device = _registry()
    manager = ExecutionRunManager(lv1_check=lambda: True, parallel_enabled=True)
    _, run = manager.try_begin(
        target=window.snapshot(), username="甲", name="窗口任务")
    assert run is not None
    host = _host(registry, manager)
    registry.select(device.id)

    assert host._start_denial() is None
    assert not manager.can_start(target_id=device.id, username="甲").allowed


def test_hotkey_start_is_blocked_by_the_same_denial() -> None:
    """F9 和托盘「开始」不能绕过灰按钮。"""
    registry, window, device = _registry()
    manager = ExecutionRunManager(lv1_check=lambda: False, parallel_enabled=True)
    manager.try_begin(target=window.snapshot(), username="甲", name="窗口任务")
    host = _host(registry, manager)
    registry.select(device.id)
    dispatched: list[str] = []
    host._plan_allows_backend = lambda: True  # type: ignore[method-assign]
    host._left_tabs = None
    host._on_run_workflow = lambda: dispatched.append("run")  # type: ignore[method-assign]
    host.log_text = type("Log", (), {"append": lambda self, text: None})()
    host.statusBar = lambda: type(  # type: ignore[method-assign]
        "Bar", (), {"showMessage": lambda self, *a: None})()

    host._on_start()

    assert dispatched == [], "并发门禁拒绝时不能真的启动"
