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


def test_device_lookup_uses_current_transport_not_target_identity() -> None:
    registry = ExecutionTargetRegistry()
    phone = ExecutionTarget(
        id=android_target_id("stable-device-id"), kind="adb",
        display_name="手机", serial="192.168.1.20:5555",
        capture=object(), input_ctrl=object(),
    )
    registry.put(phone)

    assert registry.device("192.168.1.20:5555") is phone
    assert registry.device("USB-SERIAL") is None


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

    assert snapshot.capture._resource() is capture
    assert snapshot.input_ctrl._resource() is input_ctrl
    assert snapshot.window == {"hwnd": 1, "left": 10, "top": 20}


def test_snapshot_handle_follows_reconnected_resources() -> None:
    registry = ExecutionTargetRegistry()
    original_capture = object()
    original_input = object()
    original = ExecutionTarget(
        id=android_target_id("stable"), kind="adb", display_name="设备",
        capture=original_capture, input_ctrl=original_input,
    )
    registry.put(original)
    snapshot = original.snapshot()

    replacement_capture = object()
    replacement_input = object()
    replacement = ExecutionTarget(
        id=original.id, kind="adb", display_name="设备",
        capture=replacement_capture, input_ctrl=replacement_input,
    )
    registry.put(replacement)

    assert snapshot.capture._resource() is replacement_capture
    assert snapshot.input_ctrl._resource() is replacement_input
    assert snapshot.handle.binding().generation == 2


def test_window_geometry_update_keeps_resource_generation() -> None:
    target = ExecutionTarget(
        id=WINDOW_TARGET_ID, kind="windows", display_name="游戏窗口",
        capture=object(), input_ctrl=object(),
        window={"hwnd": 1, "left": 10, "top": 20},
    )
    snapshot = target.snapshot()

    snapshot.handle.update_window({"hwnd": 2, "left": 30})

    binding = snapshot.handle.binding()
    assert binding.generation == 1
    assert binding.window == {"hwnd": 2, "left": 30, "top": 20}


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

    # serial 是名称列的内容；目标大小只显示尺寸
    assert WindowOpsMixin._target_size_details(target) == "2560×1440"
    # 连接信息固定「输入方式 · 截图方式」，每段四字，不露实现 key
    assert WindowOpsMixin._target_connection_details(target) == (
        "端侧执行 · 流式截图")


def test_window_connection_and_runtime_status_are_separate() -> None:
    target = ExecutionTarget(
        id=WINDOW_TARGET_ID,
        kind="windows",
        display_name="游戏窗口",
        capture=object(),
        input_ctrl=type("Input", (), {"background_mode": False})(),
        window={"left": 10, "top": 20, "width": 1920, "height": 1080},
    )

    assert WindowOpsMixin._target_size_details(target) == "1920×1080"
    assert WindowOpsMixin._target_connection_details(target) == (
        "前台输入 · 前台截图")


# ─── 连接信息列的取值全集 ───────────────────────────────


def _device_target(*, device_execution: bool, method: str) -> ExecutionTarget:
    return ExecutionTarget(
        id=android_target_id("A"), kind="adb", display_name="手机",
        capture=object(), input_ctrl=object(),
        agent=object() if device_execution else None,
        capture_method=method,
    )


def test_status_column_never_leaks_implementation_keys() -> None:
    """实现 key 不进 UI：这一列是给普通用户看的。"""
    for method in ("screencap", "scrcpy"):
        for device_execution in (False, True):
            text = WindowOpsMixin._target_connection_details(
                _device_target(
                    device_execution=device_execution, method=method))
            assert method not in text
            assert "ADB" not in text
