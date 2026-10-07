"""截图后端抽象基类

定义所有截图后端（桌面 mss 窗口截图 / ADB screencap）的统一公开面。
工作流与上层代码仅依赖此抽象，不感知具体实现。

子类职责：
- DesktopCapture：基于 mss + 工作线程的桌面窗口截图
- AdbCapture：基于 adb exec-out screencap -p 的设备截图
"""

import time
from abc import ABC, abstractmethod

import numpy as np


class A11yScreenshotThrottle:
    """无障碍 takeScreenshot 的请求节拍

    这是平台事实而非本项目策略：AOSP 的 AbstractAccessibilityServiceConnection
    以「距上一次请求 <=333ms」判定间隔过短，直接回
    ERROR_TAKE_SCREENSHOT_INTERVAL_TIME_SHORT；时间戳按服务连接记录，换 display
    或换线程都绕不开，被限流的那次请求不更新时间戳，所以退避一次就能成。

    设备端直连和 PC 代理两条通道打的是同一个服务连接的限流，节拍只应有一份实现：
    发请求前先等到最小间隔之上，而不是撞上失败再盲退避。走 Shizuku screencap 的
    帧不受限流，此时节拍为 0。
    """

    #: 333ms 阈值之上留的余量：请求发出时一定越过阈值。
    #: 厂商 ROM 可能把间隔调大，真遇到再按实测抬高这个值。
    MIN_REQUEST_INTERVAL = 0.35

    def _init_throttle(self, *, throttled: bool = True) -> None:
        self._last_request = 0.0
        self._set_throttled(throttled)

    def _set_throttled(self, throttled: bool) -> None:
        """按本帧实际走的通道更新采样下限（无障碍受限流，shell 不受）"""
        self.min_capture_interval = self.MIN_REQUEST_INTERVAL if throttled else 0.0

    def _pace(self) -> None:
        """等到允许发请求的时刻，并记下本次请求时间"""
        waiting = self._last_request + self.min_capture_interval - time.monotonic()
        if waiting > 0:
            time.sleep(waiting)
        self._last_request = time.monotonic()


class CaptureBackend(ABC):
    """截图后端抽象基类

    公开接口：
    - capture(timeout)：截取一帧（BGR numpy），失败返回 None
    - capture_to_file(path)：截屏并保存为 PNG 文件
    - get_capture_size()：返回当前捕获区域的宽高 (width, height)
    - set_capture_region(left, top, width, height)：设置捕获区域（ADB 为 no-op）
    - attach_to_window(title_keyword)：附着到指定窗口（ADB 为 no-op）
    - min_capture_interval：后端自身的最小截图间隔（秒），0 表示不限频
    """

    #: 后端自身的最小截图间隔（秒），0 表示不限频。
    #: Android 无障碍 ``takeScreenshot`` 由框架限流（AOSP 判定两次请求间隔
    #: <=333ms 为「间隔过短」），采样循环必须据此抬高自己的间隔；PC 后端保持
    #: 0，取 max 后行为不变。
    min_capture_interval: float = 0.0

    @abstractmethod
    def capture(self, timeout: float = 5.0) -> np.ndarray | None:
        """截取一帧屏幕/设备画面

        Args:
            timeout: 超时秒数（默认 5s）

        Returns:
            BGR numpy 数组，失败返回 None
        """

    def capture_lossless(self, timeout: float = 10.0) -> np.ndarray | None:
        """无损截图（用于 OCR 等需要高清晰度的场景）

        默认实现：与 capture() 相同。子类可覆盖以提供更高质量截图。
        例如：scrcpy 视频流后端可回退到 screencap 获取无损 PNG。

        Args:
            timeout: 超时秒数（默认 10s）

        Returns:
            BGR numpy 数组，失败返回 None
        """
        return self.capture(timeout=timeout)

    def capture_to_file(self, path: str) -> bool:
        """截屏并保存为 PNG 文件

        默认实现：capture() + cv2.imencode(.png)；桌面 mss 子类可覆盖为 mss.tools.to_png。
        """
        img = self.capture()
        if img is None:
            return False
        try:
            import cv2
            success, buf = cv2.imencode(".png", img)
            if success:
                with open(path, "wb") as f:
                    f.write(buf.tobytes())
                return True
        except Exception as e:
            from loguru import logger
            logger.error(f"保存截图失败: {e}")
        return False

    @abstractmethod
    def get_capture_size(self) -> tuple[int, int]:
        """返回当前捕获区域的宽高 (width, height) 像素

        桌面 mss：返回 set_capture_region 设置的区域宽高，或主显示器分辨率。
        ADB：返回设备物理分辨率。
        """

    # 可选 hook 而非抽象方法：ADB 等全屏后端无需覆盖
    def set_capture_region(self, left: int, top: int, width: int, height: int):  # noqa: B027
        """设置捕获区域

        桌面 mss 子类覆盖实现；ADB 子类为 no-op（设备全屏截图）。
        """
        pass

    def attach_to_window(self, title_keyword: str) -> bool:
        """通过窗口标题关键词附着到指定窗口

        桌面 mss 子类覆盖实现；ADB 子类为 no-op 返回 False。
        """
        return False

    def stop(self):  # noqa: B027
        """释放截图后端持有的后台线程、socket 或系统资源。

        默认 no-op，供无常驻资源的后端继承。UI 层在断连、切换后端和
        退出程序时会统一调用此方法。
        """
        pass
