import ctypes
from types import SimpleNamespace

from lvjiang.ui.main.execution_targets import (
    WINDOW_TARGET_ID,
    ExecutionTarget,
    ExecutionTargetRegistry,
    android_target_id,
    window_target_label,
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


def test_window_target_name_uses_process_id_not_window_handle() -> None:
    window = {"pid": 12345, "hwnd": 0x1A2B3C}

    assert window_target_label(window) == "WINDOWS:12345"


def test_refresh_window_rect_rejects_a_disappeared_window(monkeypatch) -> None:
    user32 = SimpleNamespace(IsWindow=lambda hwnd: False)
    monkeypatch.setattr(
        ctypes, "windll", SimpleNamespace(user32=user32), raising=False)
    window = {
        "pid": 12345, "hwnd": 0x1A2B3C,
        "left": 10, "top": 20, "width": 100, "height": 200,
    }

    assert not WindowOpsMixin._refresh_window_rect(object(), window)
    assert (window["left"], window["top"]) == (10, 20)


def test_refresh_window_rect_updates_a_matching_live_window(monkeypatch) -> None:
    class User32:
        @staticmethod
        def IsWindow(hwnd) -> bool:
            return True

        @staticmethod
        def GetWindowThreadProcessId(hwnd, pid_ptr) -> int:
            pid_ptr._obj.value = 12345
            return 1

        @staticmethod
        def GetWindowRect(hwnd, rect_ptr) -> bool:
            rect = rect_ptr._obj
            rect.left, rect.top, rect.right, rect.bottom = 30, 40, 330, 240
            return True

    monkeypatch.setattr(
        ctypes, "windll", SimpleNamespace(user32=User32()), raising=False)
    window = {
        "pid": 12345, "hwnd": 0x1A2B3C,
        "left": 10, "top": 20, "width": 100, "height": 200,
    }

    assert WindowOpsMixin._refresh_window_rect(object(), window)
    assert (window["left"], window["top"], window["width"], window["height"]) == (
        30, 40, 300, 200)


def test_switching_target_never_captures_a_preview() -> None:
    """目标切换必须先恢复任务视图，不能在 UI 线程同步执行 ADB 截图。"""
    registry = ExecutionTargetRegistry()
    target = ExecutionTarget(
        id=android_target_id("A"), kind="adb", display_name="设备",
        capture=object(), input_ctrl=object(), status="connected",
    )
    registry.put(target)

    class PreviewLabel:
        def clear(self) -> None:
            pass

        def setText(self, text: str) -> None:
            self.text = text

    host = type("Host", (WindowOpsMixin,), {})()
    host._execution_targets = registry
    host._preview_enabled = True
    host.preview_label = PreviewLabel()
    host._capture_preview = lambda: (_ for _ in ()).throw(AssertionError(
        "切换目标不应主动截图"))

    host._refresh_active_target_ui()

    assert host.preview_label.text == "暂无缓存画面，请点击刷新"


def test_hidden_preview_does_not_queue_scrcpy_frames() -> None:
    registry = ExecutionTargetRegistry()
    target = ExecutionTarget(
        id=android_target_id("A"), kind="adb", display_name="设备",
        capture=object(), input_ctrl=object(), status="connected",
    )
    registry.put(target)
    emitted: list[tuple] = []
    host = type("Host", (WindowOpsMixin,), {})()
    host._execution_targets = registry
    host._preview_enabled = False
    host._screen_recorder = None
    host._scrcpy_frame_ready = SimpleNamespace(
        emit=lambda *args: emitted.append(args))
    frame = object()

    host._on_scrcpy_frame(target.id, frame)

    assert target.last_capture is frame
    assert emitted == []


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


# ─── 连接信息列的运行状态配色 ───────────────────────────


def _run(state) -> SimpleNamespace:
    return SimpleNamespace(state=state)


def test_run_state_distinguishes_idle_running_paused_and_fault() -> None:
    """配色只认这四档：未运行/运行中/暂停中/异常。"""
    from lvjiang.ui.main.execution_runs import RunState

    target = _device_target(device_execution=False, method="scrcpy")
    assert WindowOpsMixin._target_run_state(target, None) == "idle"
    assert WindowOpsMixin._target_run_state(target, _run(RunState.STARTING)) == "running"
    assert WindowOpsMixin._target_run_state(target, _run(RunState.RUNNING)) == "running"
    assert WindowOpsMixin._target_run_state(target, _run(RunState.PAUSING)) == "paused"
    assert WindowOpsMixin._target_run_state(target, _run(RunState.PAUSED)) == "paused"
    assert WindowOpsMixin._target_run_state(
        target, _run(RunState.WAITING_TARGET)) == "fault"


def test_offline_target_and_stream_fault_count_as_fault() -> None:
    """离线与空闲断流都算异常——断流时目标仍「已连接」，但不能显示成正常。"""
    offline = _device_target(device_execution=False, method="scrcpy")
    offline.status = "offline"
    assert WindowOpsMixin._target_run_state(offline, None) == "fault"

    broken = _device_target(device_execution=False, method="scrcpy")
    broken.capture = SimpleNamespace(last_fault="decoder_stall")
    assert WindowOpsMixin._target_run_state(broken, None) == "fault"


def test_selected_target_row_is_never_tinted(qtbot) -> None:
    """选中行不叠状态色；取消选中后才恢复——否则选中动作会带一次颜色突变。"""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QTreeWidget

    from lvjiang.ui.main.execution_runs import RunState
    from lvjiang.ui.theme import get_theme_manager

    running = ExecutionTarget(
        id=android_target_id("A"), kind="adb", display_name="运行中设备",
        capture=object(), input_ctrl=object(), status="connected",
    )
    idle = ExecutionTarget(
        id=android_target_id("B"), kind="adb", display_name="空闲设备",
        capture=object(), input_ctrl=object(), status="connected",
    )
    registry = ExecutionTargetRegistry()
    registry.put(running)
    registry.put(idle)

    host = type("Host", (WindowOpsMixin,), {})()
    host._execution_targets = registry
    host._run_manager = SimpleNamespace(
        run_for_target=lambda tid: (
            SimpleNamespace(state=RunState.RUNNING) if tid == running.id else None))
    host._resize_execution_target_columns = lambda: None
    host.execution_target_list = QTreeWidget()
    host.execution_target_list.setColumnCount(5)

    host._refresh_execution_targets_ui()

    tree = host.execution_target_list
    items = {tree.topLevelItem(row).text(0): tree.topLevelItem(row)
             for row in range(tree.topLevelItemCount())}
    running_item, idle_item = items["运行中设备"], items["空闲设备"]
    column = WindowOpsMixin._CONNECTION_INFO_COLUMN
    # 当前查看目标是运行中那台，但它被选中 → 不着色
    assert running_item.foreground(column).style() == Qt.BrushStyle.NoBrush
    assert idle_item.foreground(column).style() == Qt.BrushStyle.NoBrush

    # 切到空闲目标：运行中那台取消选中后恢复状态色
    tree.setCurrentItem(idle_item)
    host._apply_execution_target_colors()

    assert idle_item.foreground(column).style() == Qt.BrushStyle.NoBrush
    brush = running_item.foreground(column)
    assert brush.style() == Qt.BrushStyle.SolidPattern
    assert brush.color().name().lower() == get_theme_manager().tokens.success.lower()
