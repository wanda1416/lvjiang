"""设备身份必须跨 USB / 无线一致，否则一台手机会变成两个可执行目标。

`target_id` 是目标互斥的主键：同一台设备分裂成两个目标以后，两个任务会各自
以为独占这台手机，同时对它点击。所以身份解析宁可退化成「拒绝连接」，也不能
让两个无法区分的候选同时进入可执行状态。
"""
from __future__ import annotations

import sys
from pathlib import Path

from lvjiang.core.android.device import AdbDevice

_BOOT_ID = "7f3c2a10-5b44-4f0e-9a21-1c0d8e6b4a52"


def _fake_adb(tmp_path: Path, responses: dict[str, str]) -> Path:
    """造一个假 adb：按命令尾部匹配预设输出，未命中返回空。"""
    script = tmp_path / "fake_adb.py"
    script.write_text(
        "import sys\n"
        f"responses = {responses!r}\n"
        "argv = ' '.join(sys.argv[1:])\n"
        "for key, value in responses.items():\n"
        "    if argv.endswith(key):\n"
        "        sys.stdout.write(value)\n"
        "        break\n",
        encoding="utf-8")
    launcher = tmp_path / "fake_adb.sh"
    launcher.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n',
        encoding="utf-8")
    launcher.chmod(0o755)
    return launcher


def test_android_id_is_preferred_and_marked_stable(tmp_path) -> None:
    device = AdbDevice(serial="192.168.1.9:5555", adb_path=str(_fake_adb(
        tmp_path, {"secure android_id": "a1b2c3d4e5f6\n"})))

    identity = device.get_stable_identity()

    assert identity.value == "a1b2c3d4e5f6"
    assert identity.source == "android_id"
    assert identity.stable


def test_boot_id_merges_both_transports_when_props_are_unavailable(
        tmp_path) -> None:
    """读不到 android_id / 序列号时退到内核 boot_id。

    boot_id 对本次开机的同一台设备恒定、不同设备必然不同，因此 USB 和无线两
    条 transport 会归并成同一个 target_id —— 这正是目标互斥需要的东西。代价是
    重启后换值，那是列表里多一行待断开的旧目标，而不是一台设备被两个任务抢。
    """
    responses = {
        "secure android_id": "null\n",
        "getprop ro.serialno": "\n",
        "getprop ro.boot.serialno": "unknown\n",
        "/proc/sys/kernel/random/boot_id": _BOOT_ID + "\n",
    }
    adb = str(_fake_adb(tmp_path, responses))

    usb = AdbDevice(serial="ABCD1234", adb_path=adb)
    wireless = AdbDevice(serial="192.168.1.9:5555", adb_path=adb)

    assert usb.get_stable_identity().value == _BOOT_ID
    assert wireless.get_stable_identity().value == _BOOT_ID
    from lvjiang.ui.main.execution_targets import android_target_id
    assert android_target_id(usb.get_stable_identity().value) \
        == android_target_id(wireless.get_stable_identity().value)


def test_transport_fallback_is_explicitly_unstable(tmp_path) -> None:
    """连 boot_id 都读不到时才退到 transport，并明确标成不稳定。"""
    device = AdbDevice(serial="192.168.1.9:5555", adb_path=str(_fake_adb(
        tmp_path, {"secure android_id": "null\n"})))

    identity = device.get_stable_identity()

    assert identity.value == "192.168.1.9:5555"
    assert identity.source == "transport"
    assert not identity.stable


def test_identity_is_resolved_once_and_cached(tmp_path) -> None:
    """身份解析要走 adb，不能每次用到都重读。"""
    device = AdbDevice(serial="A", adb_path=str(_fake_adb(
        tmp_path, {"secure android_id": "cached-id\n"})))

    first = device.get_stable_identity()

    assert device.get_stable_identity() is first


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
