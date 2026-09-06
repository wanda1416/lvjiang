"""Android 应用生命周期控制，供 DSL 与 Python 工作流共同调用。"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

import numpy as np
from loguru import logger

from ..config import AndroidAppConfig
from .device import AdbDevice

_PACKAGE_RE = re.compile(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+$")
_ACTIVITY_RE = re.compile(r"^[A-Za-z0-9_.$/]+$")


class AndroidAppError(RuntimeError):
    """应用配置无效、ADB 操作失败或等待超时。"""


class AndroidAppController:
    """通过同一个 :class:`AdbDevice` 控制已注册应用。"""

    def __init__(
        self,
        device: AdbDevice,
        apps: dict[str, AndroidAppConfig],
        *,
        capture=None,
        stop_check: Callable[[], bool] | None = None,
    ):
        self.device = device
        self.apps = apps
        self.capture = capture
        self.stop_check = stop_check or (lambda: False)

    def get(self, name: str) -> AndroidAppConfig:
        alias = str(name or "").strip()
        app = self.apps.get(alias)
        if app is None:
            raise AndroidAppError(f"未注册安卓应用: {alias!r}，请先在配置管理→安卓设置中添加")
        if not _PACKAGE_RE.fullmatch(app.package):
            raise AndroidAppError(f"安卓应用 {alias!r} 的包名无效: {app.package!r}")
        if app.activity and not _ACTIVITY_RE.fullmatch(app.activity):
            raise AndroidAppError(f"安卓应用 {alias!r} 的 Activity 无效: {app.activity!r}")
        return app

    def _cancelled(self) -> None:
        if self.stop_check():
            raise AndroidAppError("用户已停止工作流")

    def _wait_until(self, predicate, timeout: float, message: str) -> None:
        deadline = time.monotonic() + max(0.1, float(timeout))
        while time.monotonic() < deadline:
            self._cancelled()
            if predicate():
                return
            time.sleep(0.25)
        raise AndroidAppError(message)

    def is_running(self, name: str) -> bool:
        app = self.get(name)
        output = self.device.shell("pidof", app.package, timeout=5.0)
        return bool(output.strip())

    def stop(self, name: str, timeout: float = 15.0) -> bool:
        app = self.get(name)
        logger.info(f"[AndroidApp] 停止 {name} ({app.package})")
        self.device.shell("am", "force-stop", app.package, timeout=10.0)
        self._wait_until(
            lambda: not self.is_running(name), timeout,
            f"停止安卓应用 {name!r} 超时：进程仍在运行",
        )
        return True

    @staticmethod
    def _component(app: AndroidAppConfig) -> str:
        activity = app.activity
        if not activity:
            return ""
        if "/" in activity:
            return activity
        if activity.startswith("."):
            return f"{app.package}/{activity}"
        return f"{app.package}/{activity}"

    def start(self, name: str, timeout: float = 30.0) -> bool:
        app = self.get(name)
        component = self._component(app)
        logger.info(f"[AndroidApp] 启动 {name} ({app.package})")
        if component:
            output = self.device.shell(
                "am", "start", "-n", component, timeout=min(timeout, 15.0),
            )
            if "Error:" in output:
                raise AndroidAppError(f"启动安卓应用 {name!r} 失败: {output}")
        else:
            output = self.device.shell(
                "monkey", "-p", app.package, "-c",
                "android.intent.category.LAUNCHER", "1",
                timeout=min(timeout, 30.0),
            )
            if "No activities found" in output or "monkey aborted" in output.lower():
                raise AndroidAppError(f"启动安卓应用 {name!r} 失败: {output}")
        self._wait_until(
            lambda: self.is_running(name), timeout,
            f"启动安卓应用 {name!r} 超时：未检测到进程",
        )
        return True

    def wait_stable_frame(
        self,
        name: str,
        timeout: float = 60.0,
        stable_duration: float = 1.0,
        interval: float = 0.2,
        threshold: float = 2.0,
    ) -> bool:
        """等待期望方向的连续有效帧，并确认画面在一段时间内稳定。"""
        app = self.get(name)
        if self.capture is None:
            raise AndroidAppError("当前工作流没有截图后端，无法等待稳定帧")
        deadline = time.monotonic() + max(0.1, float(timeout))
        wait_ready = getattr(self.capture, "wait_ready", None)
        if callable(wait_ready):
            remaining = max(0.1, deadline - time.monotonic())
            if not wait_ready(remaining, expected_orientation=app.orientation):
                raise AndroidAppError(
                    f"等待安卓应用 {name!r} 画面超时：视频流未恢复或方向不符")

        previous = None
        previous_sequence = None
        stable_since = None
        while time.monotonic() < deadline:
            self._cancelled()
            sequence = getattr(self.capture, "frame_sequence", None)
            if sequence is not None and sequence == previous_sequence:
                time.sleep(interval)
                continue
            frame = self.capture.capture(timeout=min(interval + 1.0, 5.0))
            if frame is None or frame.size == 0:
                time.sleep(interval)
                continue
            previous_sequence = sequence
            h, w = frame.shape[:2]
            if app.orientation == "landscape" and w <= h:
                previous = None
                stable_since = None
                time.sleep(interval)
                continue
            if app.orientation == "portrait" and h <= w:
                previous = None
                stable_since = None
                time.sleep(interval)
                continue
            # 小图比较足以判断启动动画是否停止，也避免高清帧逐像素 diff。
            sample = frame[::max(1, h // 180), ::max(1, w // 320)].astype(np.int16)
            if previous is not None and previous.shape == sample.shape:
                diff = float(np.mean(np.abs(sample - previous)))
                if diff <= threshold:
                    stable_since = stable_since or time.monotonic()
                    if time.monotonic() - stable_since >= stable_duration:
                        logger.info(f"[AndroidApp] {name} 已收到稳定帧 {w}x{h}")
                        return True
                else:
                    stable_since = None
            previous = sample
            time.sleep(max(0.02, interval))
        raise AndroidAppError(f"等待安卓应用 {name!r} 稳定帧超时")
