from __future__ import annotations

from types import SimpleNamespace

from lvjiang.core.android.device import AdbDevice
from lvjiang.core.android.scrcpy_capture import AndroidStreamCapture


def test_forward_dynamic_returns_adb_allocated_port(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        return SimpleNamespace(returncode=0, stdout="38127\n", stderr="")

    monkeypatch.setattr("lvjiang.core.android.device.subprocess.run", fake_run)

    device = AdbDevice("device-a", adb_path="adb")
    assert device.forward_dynamic("localabstract:scrcpy_1234") == 38127
    assert calls == [[
        "adb", "-s", "device-a", "forward", "tcp:0",
        "localabstract:scrcpy_1234",
    ]]


def test_forward_dynamic_rejects_missing_port(monkeypatch):
    monkeypatch.setattr(
        "lvjiang.core.android.device.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout="", stderr=""),
    )

    assert AdbDevice("device-a", adb_path="adb").forward_dynamic(
        "localabstract:scrcpy_1234") is None


def test_scrcpy_cleanup_removes_only_its_own_forward():
    class Device:
        def __init__(self):
            self.removed: list[str] = []

        def remove_forward(self, local: str) -> None:
            self.removed.append(local)

    device = Device()
    capture = AndroidStreamCapture(device)  # type: ignore[arg-type]
    capture._video_port = 38127

    capture._cleanup_forward()
    capture._cleanup_forward()

    assert device.removed == ["tcp:38127"]
    assert capture._video_port is None


def test_device_identity_is_independent_from_transport(monkeypatch):
    device = AdbDevice("192.168.1.20:5555", adb_path="adb")
    calls: list[tuple[str, ...]] = []

    def fake_shell(*args: str, timeout: float = 0) -> str:
        calls.append(args)
        assert timeout == 5.0
        return "stable-android-id\n"

    monkeypatch.setattr(device, "shell", fake_shell)

    first = device.get_stable_identity()
    second = device.get_stable_identity()

    assert first == second
    assert first.value == "stable-android-id"
    assert first.source == "android_id" and first.stable is True
    assert calls == [("settings", "get", "secure", "android_id")]


def test_device_identity_falls_back_when_a_source_cannot_be_read(monkeypatch):
    device = AdbDevice("usb-transport", adb_path="adb")

    def fake_shell(*args: str, timeout: float = 0) -> str:
        assert timeout == 5.0
        if args[0] == "settings":
            raise RuntimeError("settings unavailable")
        return "hardware-serial\n"

    monkeypatch.setattr(device, "shell", fake_shell)

    identity = device.get_stable_identity()

    assert identity.value == "hardware-serial"
    assert identity.source == "ro.serialno"
    assert identity.stable is True
