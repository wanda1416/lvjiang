"""设备身份必须跨 USB / 无线一致，否则一台手机会变成两个可执行目标。

`target_id` 是目标互斥的主键：同一台设备分裂成两个目标以后，两个任务会各自
以为独占这台手机，同时对它点击。所以身份解析宁可退化成「拒绝连接」，也不能
让两个无法区分的候选同时进入可执行状态。
"""
from __future__ import annotations

# ─── 身份未确认的目标不能并存 ───────────────────────────


def _unstable(target_id: str, name: str):
    from lvjiang.ui.main.execution_targets import ExecutionTarget
    return ExecutionTarget(
        id=target_id, kind="adb", display_name=name,
        capture=object(), input_ctrl=object(),
        metadata={"device_identity_stable": False},
    )


def _stable(target_id: str, name: str):
    from lvjiang.ui.main.execution_targets import ExecutionTarget
    return ExecutionTarget(
        id=target_id, kind="adb", display_name=name,
        capture=object(), input_ctrl=object(),
        metadata={"device_identity_stable": True},
    )


def test_two_unconfirmed_devices_cannot_both_become_executable() -> None:
    """两个只能靠连接地址区分的候选不允许同时可执行。

    放行就可能让同一台设备变成两个目标，两个任务各自以为独占它；自动归并又
    可能把两台设备错当成一台。两者都不能猜，所以拦在登记之前。
    """
    from lvjiang.ui.main.execution_targets import ExecutionTargetRegistry
    from lvjiang.ui.main.window_ops import WindowOpsMixin

    registry = ExecutionTargetRegistry()
    first = _unstable("android:usb", "手机 A")
    registry.put(first)
    host = type("Host", (WindowOpsMixin,), {})()
    host._execution_targets = registry

    conflict = WindowOpsMixin._unconfirmed_identity_conflict(
        host, "android:wireless", False)

    assert conflict is first


def test_local_emulator_instances_get_distinct_target_ids() -> None:
    """本机模拟器多开的实例按端口区分，可以同时并存。

    MuMu 多开由同一份镜像克隆而来，`android_id` 完全相同（实测四个实例取值
    一致），只按稳定身份算 ID 会让后连的实例覆盖先连的实例。
    """
    from lvjiang.ui.main.execution_targets import android_target_id

    identity = "a5924625343b460"
    assert android_target_id(identity, "127.0.0.1:7555") != \
        android_target_id(identity, "127.0.0.1:5555")
    assert android_target_id(identity, "emulator-5554") != \
        android_target_id(identity, "emulator-5556")


def test_physical_device_transports_still_merge() -> None:
    """真机的 USB 与无线通道仍归并为一个目标，地址不参与身份。"""
    from lvjiang.ui.main.execution_targets import android_target_id

    identity = "physical-android-id"
    assert android_target_id(identity, "192.168.1.20:5555") == \
        android_target_id(identity, "R58M123ABC")
    assert android_target_id(identity, "192.168.1.20:5555") == \
        android_target_id(identity)


def test_stable_identities_never_block_each_other() -> None:
    """有稳定标识的设备照常多连；拦的只是无法区分的那一类。"""
    from lvjiang.ui.main.execution_targets import ExecutionTargetRegistry
    from lvjiang.ui.main.window_ops import WindowOpsMixin

    registry = ExecutionTargetRegistry()
    registry.put(_stable("android:aaa", "手机 A"))
    registry.put(_unstable("android:usb", "手机 B"))
    host = type("Host", (WindowOpsMixin,), {})()
    host._execution_targets = registry

    assert WindowOpsMixin._unconfirmed_identity_conflict(
        host, "android:bbb", True) is None
    # 同一个目标重连（同 ID 换 transport）不算冲突
    assert WindowOpsMixin._unconfirmed_identity_conflict(
        host, "android:usb", False) is None
