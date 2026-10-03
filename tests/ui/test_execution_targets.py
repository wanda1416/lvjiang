from lvjiang.ui.main.execution_targets import (
    WINDOW_TARGET_ID,
    ExecutionTarget,
    ExecutionTargetRegistry,
    android_target_id,
)
from lvjiang.ui.main.window_ops import WindowOpsMixin


def _target(target_id: str, kind: str, name: str) -> ExecutionTarget:
    return ExecutionTarget(
        id=target_id,
        kind=kind,  # type: ignore[arg-type]
        display_name=name,
        capture=object(),
        input_ctrl=object(),
    )


def test_registry_keeps_one_window_and_multiple_devices() -> None:
    registry = ExecutionTargetRegistry()
    window = _target(WINDOW_TARGET_ID, "windows", "游戏窗口")
    phone_a = _target(android_target_id("A"), "adb", "手机 A")
    phone_b = _target(android_target_id("B"), "adb", "手机 B")

    registry.put(window)
    registry.put(phone_a)
    registry.put(phone_b)

    assert registry.active() is window
    assert {target.id for target in registry.all()} == {
        WINDOW_TARGET_ID,
        android_target_id("A"),
        android_target_id("B"),
    }


def test_connecting_another_target_does_not_steal_active_selection() -> None:
    registry = ExecutionTargetRegistry()
    phone_a = _target(android_target_id("A"), "adb", "手机 A")
    phone_b = _target(android_target_id("B"), "adb", "手机 B")
    registry.put(phone_a)
    registry.put(phone_b)

    assert registry.active() is phone_a
    registry.select(phone_b.id)
    replacement = _target(android_target_id("A"), "adb", "手机 A（重连）")
    assert registry.put(replacement) is phone_a
    assert registry.active() is phone_b


def test_removing_active_target_selects_a_remaining_target() -> None:
    registry = ExecutionTargetRegistry()
    window = _target(WINDOW_TARGET_ID, "windows", "游戏窗口")
    phone = _target(android_target_id("A"), "adb", "手机 A")
    registry.put(window)
    registry.put(phone)
    registry.select(phone.id)

    assert registry.remove(phone.id) is phone
    assert registry.active() is window


def test_snapshot_freezes_window_coordinates_and_backend_references() -> None:
    capture = object()
    input_ctrl = object()
    target = ExecutionTarget(
        id=WINDOW_TARGET_ID,
        kind="windows",
        display_name="游戏窗口",
        capture=capture,
        input_ctrl=input_ctrl,
        window={"hwnd": 1, "left": 10, "top": 20},
    )

    snapshot = target.snapshot()
    target.window["left"] = 99

    assert snapshot.capture is capture
    assert snapshot.input_ctrl is input_ctrl
    assert snapshot.window == {"hwnd": 1, "left": 10, "top": 20}


def test_android_connection_and_runtime_status_are_separate() -> None:
    target = ExecutionTarget(
        id=android_target_id("serial-1"),
        kind="adb",
        display_name="手机",
        capture=object(),
        input_ctrl=object(),
        serial="serial-1",
        width=2560,
        height=1440,
        capture_method="scrcpy",
        agent=object(),
    )

    assert WindowOpsMixin._target_connection_details(target) == (
        "serial-1 · 2560×1440")
    assert WindowOpsMixin._target_status_details(target) == (
        "scrcpy · 设备端执行")


def test_window_connection_and_runtime_status_are_separate() -> None:
    target = ExecutionTarget(
        id=WINDOW_TARGET_ID,
        kind="windows",
        display_name="游戏窗口",
        capture=object(),
        input_ctrl=type("Input", (), {"background_mode": False})(),
        window={"left": 10, "top": 20, "width": 1920, "height": 1080},
    )

    assert WindowOpsMixin._target_connection_details(target) == (
        "起点 (10, 20) · 1920×1080")
    assert WindowOpsMixin._target_status_details(target) == (
        "前台模式 · 前台截图")
