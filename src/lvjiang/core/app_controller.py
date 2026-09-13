"""按应用注册类型调度 Android 与 Windows 应用生命周期。"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from loguru import logger

from .android.app_controller import AndroidAppController, AndroidAppError
from .config import AndroidAppConfig


class AppControlError(RuntimeError):
    """应用不存在、配置无效或生命周期操作失败。"""


_observed_window: dict | None = None
_connected_apps: dict[str, dict] = {}
_connected_apps_lock = threading.Lock()
_active_connection_platform = ""


def record_connected_window(window: dict) -> None:
    """记录最近一次窗口模式定位到的真实窗口身份与位置。"""
    global _observed_window, _active_connection_platform
    _observed_window = dict(window)
    _active_connection_platform = "pc"
    with _connected_apps_lock:
        _connected_apps["pc"] = {
            "platform": "pc",
            "executable": str(window.get("executable") or ""),
            "window_title": str(window.get("title") or ""),
            "hwnd": int(window.get("hwnd") or 0),
            "pid": int(window.get("pid") or 0),
            "left": window.get("left"),
            "top": window.get("top"),
            "width": window.get("width"),
            "height": window.get("height"),
        }


def record_connected_android(device, *, width: int = 0, height: int = 0) -> dict:
    """查询并记录 ADB 设备当前前台应用。"""
    global _active_connection_platform
    import re

    output = device.shell("dumpsys", "activity", "activities", timeout=5.0)
    match = re.search(
        r"mResumedActivity[^\n]*?\s([A-Za-z0-9_.]+)/([A-Za-z0-9_.$]+)",
        output,
    )
    if match is None:
        output = device.shell("dumpsys", "window", "windows", timeout=5.0)
        match = re.search(
            r"mCurrentFocus[^\n]*?\s([A-Za-z0-9_.]+)/([A-Za-z0-9_.$]+)",
            output,
        )
    info = {
        "platform": "android",
        "package": match.group(1) if match else "",
        "activity": match.group(2) if match else "",
        "orientation": (
            "landscape" if width > height else "portrait" if height > width else "any"),
        "serial": str(getattr(device, "serial", "") or ""),
    }
    with _connected_apps_lock:
        _connected_apps["android"] = info
        _active_connection_platform = "android"
    return dict(info)


def get_connected_app_info(platform: str) -> dict | None:
    """返回最近一次真实连接自动采集的信息副本。"""
    with _connected_apps_lock:
        value = _connected_apps.get(str(platform or "").lower())
        return dict(value) if value is not None else None


def get_active_connected_app_info() -> dict | None:
    """返回当前实际连接目标的信息，不使用工作流 env 推断。"""
    with _connected_apps_lock:
        value = _connected_apps.get(_active_connection_platform)
        return dict(value) if value is not None else None


class WindowsAppController:
    def __init__(self, apps: dict[str, AndroidAppConfig], *,
                 stop_check: Callable[[], bool] | None = None):
        self.apps = apps
        self.stop_check = stop_check or (lambda: False)
        self._placements: dict[str, dict] = {}

    def get(self, name: str) -> AndroidAppConfig:
        app = self.apps.get(str(name or "").strip())
        if app is None:
            raise AppControlError(f"未注册应用: {name!r}")
        return app

    @staticmethod
    def _windows() -> list[dict]:
        if sys.platform != "win32":
            return []
        from .desktop.win32_util import list_visible_windows
        return list_visible_windows()

    def _matches(self, app: AndroidAppConfig, window: dict) -> bool:
        expected = (os.path.normcase(os.path.abspath(app.executable))
                    if app.executable else "")
        actual = window.get("executable")
        if expected and actual and os.path.normcase(os.path.abspath(actual)) == expected:
            return True
        return bool(app.window_title and app.window_title in window.get("title", ""))

    def _find(self, app: AndroidAppConfig) -> dict | None:
        observed = _observed_window
        pc_apps = [item for item in self.apps.values()
                   if item.platform in {"pc", "both"}]
        if observed is not None and (
                self._matches(app, observed)
                or (len(pc_apps) == 1
                    and not app.executable and not app.window_title)):
            if sys.platform != "win32" or self._window_is_current(observed):
                if not app.executable and observed.get("executable"):
                    # 运行期绑定：窗口模式首次定位即成为该注册项后续启停依据。
                    app.executable = str(observed["executable"])
                    app.window_title = str(observed.get("title") or "")
                return dict(observed)
        return next((item for item in self._windows() if self._matches(app, item)), None)

    @classmethod
    def _window_is_current(cls, window: dict) -> bool:
        if not cls._pid_running(window.get("pid", 0)):
            return False
        import ctypes
        hwnd = int(window.get("hwnd", 0))
        if not hwnd or not ctypes.windll.user32.IsWindow(hwnd):
            return False
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value) == int(window.get("pid", 0))

    @staticmethod
    def _pid_running(pid: int) -> bool:
        if not pid:
            return False
        if sys.platform != "win32":
            return False
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x100000, False, int(pid))
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True

    def is_running(self, name: str) -> bool:
        return self._find(self.get(name)) is not None

    def stop(self, name: str, timeout: float = 15.0) -> bool:
        app = self.get(name)
        window = self._find(app)
        if window is None:
            return True
        self._placements[name] = {
            key: window.get(key) for key in ("left", "top", "width", "height")}
        if sys.platform != "win32":
            raise AppControlError("PC 应用控制仅支持 Windows")
        import ctypes
        ctypes.windll.user32.PostMessageW(int(window["hwnd"]), 0x0010, 0, 0)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.stop_check():
                raise AppControlError("用户已停止工作流")
            if self._find(app) is None:
                return True
            time.sleep(0.25)
        subprocess.run(
            ["taskkill", "/PID", str(window["pid"]), "/T", "/F"],
            check=False, capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if self._find(app) is not None:
            raise AppControlError(f"停止 PC 应用 {name!r} 超时")
        return True

    def start(self, name: str, timeout: float = 30.0) -> bool:
        app = self.get(name)
        if self._find(app) is not None:
            return True
        if sys.platform != "win32":
            raise AppControlError("PC 应用控制仅支持 Windows")
        observed = _observed_window or {}
        executable = app.executable or str(observed.get("executable") or "")
        if not executable:
            raise AppControlError(
                f"PC 应用 {name!r} 尚未绑定窗口；请先用窗口模式定位该应用")
        executable = str(Path(executable))
        # 通过 Explorer 的 Shell.Application COM 服务真正发起启动。直接
        # Popen/CREATE_NEW_PROCESS_GROUP 只能脱离控制台，目标的创建者仍是本进程。
        # PowerShell 只是一次性 COM 客户端；目标由 Explorer shell 创建。
        def _ps_quote(value: str) -> str:
            return "'" + value.replace("'", "''") + "'"

        arguments = subprocess.list2cmdline(app.arguments)
        working_dir = str(Path(executable).parent)
        script = (
            "$shell=New-Object -ComObject Shell.Application;"
            f"$shell.ShellExecute({_ps_quote(executable)},"
            f"{_ps_quote(arguments)},{_ps_quote(working_dir)},'open',1)"
        )
        flags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=flags,
        )
        deadline = time.monotonic() + timeout
        window = None
        while time.monotonic() < deadline:
            if self.stop_check():
                raise AppControlError("用户已停止工作流")
            window = self._find(app)
            if window is not None:
                break
            time.sleep(0.25)
        if window is None:
            raise AppControlError(f"启动 PC 应用 {name!r} 超时：未发现目标窗口")
        placement = self._placements.get(name)
        if placement is None and observed:
            placement = {
                key: observed.get(key)
                for key in ("left", "top", "width", "height")}
        if placement and all(value is not None for value in placement.values()):
            import ctypes
            ctypes.windll.user32.SetWindowPos(
                int(window["hwnd"]), 0, placement["left"], placement["top"],
                placement["width"], placement["height"], 0x0014)
        logger.info(f"[PCApp] 已启动 {name} ({executable})")
        return True


class AppController:
    """根据实际连接目标选择同一应用的 Android/Windows 绑定。"""

    def __init__(self, apps: dict[str, AndroidAppConfig], *, device=None,
                 capture=None, stop_check: Callable[[], bool] | None = None):
        self.apps = apps
        self._android = (AndroidAppController(
            device, apps, capture=capture, stop_check=stop_check)
            if device is not None else None)
        self._windows = WindowsAppController(apps, stop_check=stop_check)

    def _target(self, name: str):
        app = self.apps.get(str(name or "").strip())
        if app is None:
            raise AppControlError(f"未注册应用: {name!r}")
        platform = _active_connection_platform
        if platform == "pc":
            return self._windows
        if platform == "android":
            if self._android is None:
                raise AppControlError(
                    f"控制 Android 应用 {name!r} 前必须连接 ADB 设备")
            return self._android
        has_android = bool(app.package)
        has_windows = bool(app.executable or app.window_title)
        if has_windows and not has_android:
            return self._windows
        if self._android is None:
            raise AppControlError(f"控制 Android 应用 {name!r} 前必须连接 ADB 设备")
        return self._android

    def is_running(self, name: str) -> bool:
        try:
            return self._target(name).is_running(name)
        except AndroidAppError as exc:
            raise AppControlError(str(exc)) from exc

    def stop(self, name: str, timeout: float = 15.0) -> bool:
        try:
            return self._target(name).stop(name, timeout)
        except AndroidAppError as exc:
            raise AppControlError(str(exc)) from exc

    def start(self, name: str, timeout: float = 30.0) -> bool:
        try:
            return self._target(name).start(name, timeout)
        except AndroidAppError as exc:
            raise AppControlError(str(exc)) from exc


__all__ = [
    "AppController", "AppControlError", "get_active_connected_app_info",
    "get_connected_app_info",
    "record_connected_android", "record_connected_window",
]
