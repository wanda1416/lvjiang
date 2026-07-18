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
        """预热：抓一帧确认设备可响应，并确定实际截图尺寸

        screencap 无需常驻进程。注意：设备物理分辨率（wm size）可能与实际
        截图尺寸不一致（如游戏强制横屏时，wm size 为竖屏 1260x2800，而
        screencap 输出横屏 2800x1260）。get_capture_size 必须以实际截图为准，
        因此这里直接抓一帧来确定尺寸。
        """
        try:
            img = self.capture()
            if img is None:
                logger.error("adb screencap 预热失败：无法获取截图")
                return False
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
            # screencap 输出的坐标系与 input tap 使用的坐标系一致（游戏横屏时
            # 二者都是 2800x1260）。因此不做任何旋转，直接返回原图。
            # get_capture_size 以“实际截图尺寸”为准，而非 wm size 物理竖屏分辨率，
            # 否则归一化坐标会用错基准导致点击偏移。
            h, w = img.shape[:2]
            self._size = (w, h)
            return img
        except Exception as e:
            logger.error(f"screencap 解码失败: {e}")
            return None

    def get_capture_size(self) -> tuple[int, int]:
        """返回实际截图尺寸（width, height）"""
        if self._size is None:
            img = self.capture()
            if img is None:
                # 退化兜底：无法截图时用物理分辨率（可能方向不符）
                return self._device.get_resolution()
        return self._size

    # set_capture_region / attach_to_window 沿用 CaptureBackend 默认 no-op
