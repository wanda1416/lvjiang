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
    # 状态信息固定「输入方式 · 截图方式」，每段四字，不露实现 key
    assert WindowOpsMixin._target_status_details(target) == (
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

    assert WindowOpsMixin._target_connection_details(target) == (
        "起点 (10, 20) · 1920×1080")
    assert WindowOpsMixin._target_status_details(target) == (
        "前台输入 · 前台截图")


# ─── 状态信息列的取值全集 ───────────────────────────────


def _window_target(*, background_input: bool, wgc: bool) -> ExecutionTarget:
    from lvjiang.core.desktop import WgcCapture
    return ExecutionTarget(
        id=WINDOW_TARGET_ID, kind="windows", display_name="游戏窗口",
        input_ctrl=type("Input", (), {"background_mode": background_input})(),
        # 只看类型，不建真实后端（Linux 上 WGC 起不来）
        capture=WgcCapture.__new__(WgcCapture) if wgc else object(),
    )


def _device_target(*, device_execution: bool, method: str) -> ExecutionTarget:
    return ExecutionTarget(
        id=android_target_id("A"), kind="adb", display_name="手机",
        capture=object(), input_ctrl=object(),
        agent=object() if device_execution else None,
        capture_method=method,
    )


def test_status_column_is_always_four_characters_per_segment() -> None:
    """状态信息固定「输入方式 · 截图方式」，两段都是四个汉字。

    这一列原来一段讲「模式」一段讲「截图」，对不上维度；ADB 分支还是反序，
    并且把 `screencap` / `scrcpy` / `ADB` 这些实现 key 原样摆给用户看。四字
    对齐之后列宽恒定，也和连接选项那两个复选框槽位（槽 1 怎么操作、槽 2 怎么
    取画面）同序，两处可以直接对读。
    """
    cases = {
        # 前台输入 + 后台截图不可达：关掉后台输入会连带关掉 WGC 截图
        ("windows", False, False): "前台输入 · 前台截图",
        ("windows", True, False): "后台输入 · 前台截图",
        ("windows", True, True): "后台输入 · 后台截图",
        ("adb", False, "screencap"): "指令执行 · 单帧截图",
        ("adb", False, "scrcpy"): "指令执行 · 流式截图",
        ("adb", True, "screencap"): "端侧执行 · 单帧截图",
        ("adb", True, "scrcpy"): "端侧执行 · 流式截图",
    }
    for (kind, flag, capture), expected in cases.items():
        target = _window_target(background_input=flag, wgc=capture) \
            if kind == "windows" \
            else _device_target(device_execution=flag, method=str(capture))

        actual = WindowOpsMixin._target_status_details(target)

        assert actual == expected, (kind, flag, capture)
        head, _, tail = actual.partition(" · ")
        assert len(head) == 4 and len(tail) == 4, actual


def test_status_column_never_leaks_implementation_keys() -> None:
    """实现 key 不进 UI：这一列是给普通用户看的。"""
    for method in ("screencap", "scrcpy"):
        for device_execution in (False, True):
            text = WindowOpsMixin._target_status_details(
                _device_target(
                    device_execution=device_execution, method=method))
            assert method not in text
            assert "ADB" not in text
