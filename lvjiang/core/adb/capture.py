"""ADB 截图后端 - 基于 adb exec-out screencap -p

直接调用设备原生 screencap 命令获取 PNG 帧，绕过 minicap 对高版本 Android 的兼容性问题。
每次 capture() 走一次 adb 子进程，延迟约 300–800ms/帧，适合工作流场景（非实时流）。

上层（工作流引擎、OCR、材料识别、区域编辑器）无需改动：
- capture() 返回最新帧（BGR numpy）
- get_capture_size() 返回设备物理分辨率
- set_capture_region()/attach_to_window() 沿用 CaptureBackend 默认 no-op
"""

import numpy as np
from loguru import logger

from ..capture_base import CaptureBackend
from .device import AdbDevice


class AdbCapture(CaptureBackend):
    """基于 adb screencap 的截图后端（接口继承 CaptureBackend）"""

    def __init__(self, device: AdbDevice):
        self._device = device
        self._size: tuple[int, int] | None = None

    # ─── 生命周期 ─────────────────────────────────────────────

    def start(self) -> bool:
        """预热：刷新设备分辨率缓存，确认设备可响应

        screencap 无需常驻进程，start() 仅做可用性检查。
        """
        try:
            w, h = self._device.get_resolution()
            if w <= 0 or h <= 0:
                logger.error("无法获取设备分辨率")
                return False
            self._size = (w, h)
            return True
        except Exception as e:
            logger.error(f"adb screencap 预热失败: {e}")
            return False

    def stop(self):
        """screencap 无常驻资源，no-op"""
        pass

    # ─── 截图接口（继承 CaptureBackend）────────────────────────

    def capture(self, timeout: float = 10.0) -> np.ndarray | None:
        """执行一次 adb exec-out screencap -p，解码 PNG 为 BGR numpy

        Args:
            timeout: 单次 screencap 超时秒数（默认 10s，screencap 比 minicap 慢）
        """
        try:
            png_bytes = self._device.shell_bytes("exec-out", "screencap", "-p", timeout=timeout)
        except Exception as e:
            logger.error(f"adb screencap 执行异常: {e}")
            return None

        if not png_bytes:
            logger.error("adb screencap 返回空数据")
            return None

        try:
            import cv2
            arr = np.frombuffer(png_bytes, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                logger.error("PNG 解码失败")
                return None
            # 缓存分辨率
            if self._size is None:
                h, w = img.shape[:2]
                self._size = (w, h)
            return img
        except Exception as e:
            logger.error(f"screencap 解码失败: {e}")
            return None

    def get_capture_size(self) -> tuple[int, int]:
        """返回设备物理分辨率（width, height）"""
        if self._size is None:
            self._size = self._device.get_resolution()
        return self._size

    # set_capture_region / attach_to_window 沿用 CaptureBackend 默认 no-op
