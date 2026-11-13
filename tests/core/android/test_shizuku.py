"""Shizuku 服务自动启动（core/android/shizuku.py）测试

没有真机：用 AdbDevice 桩记录 shell 调用并脚本化返回，覆盖：
- 只读检测 is_running（pidof shizuku_server）
- 动态解析 libshizuku.so 路径（pm path + ABI 映射）
- 幂等：已在运行时不启动
- 动态拼接 native lib 路径并后台启动
- 未安装 / 启动后仍未就绪的失败边界（不抛异常）
"""
from __future__ import annotations

from lvjiang.core.android import shizuku
from lvjiang.core.android.device import AdbDevice


class _FakeDevice(AdbDevice):
    """记录 shell 调用并脚本化返回的 AdbDevice 桩。"""

    def __init__(self, *, pidof_values=(), pm_path="", abi="arm64-v8a"):
        super().__init__(serial="test-device", adb_path="test-adb")
        self._pidof_values = list(pidof_values)
        self._pm_path = pm_path
        self._abi = abi
        self.shell_calls: list[tuple] = []
        self.launches: list[str] = []

    def shell(self, *args, timeout=15.0, retry_safe=False):
        self.shell_calls.append(args)
        if args and args[0] == "pidof":
            return self._pidof_values.pop(0) if self._pidof_values else ""
        if args and args[0] == "pm":
            return self._pm_path
        # 其余为单字符串后台启动命令
        self.launches.append(args[0])
        return ""

    def get_abi(self):
        return self._abi


def test_is_running_uses_pidof():
    dev = _FakeDevice(pidof_values=["20213"])
    assert shizuku.is_running(dev) is True
    assert shizuku.is_running(_FakeDevice(pidof_values=[""])) is False


def test_resolve_native_lib_maps_base_apk_to_lib():
    dev = _FakeDevice(
        pm_path="package:/data/app/~~AAA/moe.shizuku.privileged.api-BBB/base.apk",
        abi="arm64-v8a",
    )
    assert shizuku._resolve_native_lib(dev) == (
        "/data/app/~~AAA/moe.shizuku.privileged.api-BBB/lib/arm64/libshizuku.so"
    )


def test_resolve_native_lib_not_installed():
    assert shizuku._resolve_native_lib(_FakeDevice(pm_path="")) is None


def test_ensure_skips_when_already_running():
    dev = _FakeDevice(pidof_values=["20213"])
    ok, msg = shizuku.ensure_shizuku_running(dev)
    assert ok is True
    assert "已在运行" in msg
    assert dev.launches == []  # 不重复启动


def test_ensure_starts_via_native_lib():
    dev = _FakeDevice(
        pidof_values=["", "9999"],  # 首次未运行，启动后轮询命中
        pm_path="package:/data/app/~~AAA/moe.shizuku.privileged.api-BBB/base.apk",
        abi="arm64-v8a",
    )
    ok, msg = shizuku.ensure_shizuku_running(dev)
    assert ok is True
    assert "已自动启动" in msg
    assert dev.launches == [
        "/data/app/~~AAA/moe.shizuku.privileged.api-BBB/lib/arm64/libshizuku.so "
        ">/dev/null 2>&1 &",
    ]


def test_ensure_reports_not_installed_without_launch():
    dev = _FakeDevice(pidof_values=[""], pm_path="")
    ok, msg = shizuku.ensure_shizuku_running(dev)
    assert ok is False
    assert "未检测到 Shizuku" in msg
    assert dev.launches == []


def test_ensure_reports_not_ready_on_timeout(monkeypatch):
    # 启动后轮询始终未就绪：不抛异常，返回说明
    monkeypatch.setattr(shizuku.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        shizuku.time, "monotonic", iter([0.0, 0.0, 1.0]).__next__)
    dev = _FakeDevice(
        pidof_values=["", ""],  # 首次未运行；启动后仍查不到
        pm_path="package:/data/app/~~AAA/moe.shizuku.privileged.api-BBB/base.apk",
    )
    ok, msg = shizuku.ensure_shizuku_running(dev, timeout=0.5)
    assert ok is False
    assert "仍未就绪" in msg
