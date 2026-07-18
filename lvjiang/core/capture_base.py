"""截图后端抽象基类

定义所有截图后端（桌面 mss 窗口截图 / ADB screencap）的统一公开面。
工作流与上层代码仅依赖此抽象，不感知具体实现。

子类职责：
- DesktopCapture：基于 mss + 工作线程的桌面窗口截图
- AdbCapture：基于 adb exec-out screencap -p 的设备截图
"""

from abc import ABC, abstractmethod

import numpy as np


class CaptureBackend(ABC):
    """截图后端抽象基类

    公开接口：
    - capture(timeout)：截取一帧（BGR numpy），失败返回 None
    - capture_to_file(path)：截屏并保存为 PNG 文件
    - get_capture_size()：返回当前捕获区域的宽高 (width, height)
    - set_capture_region(left, top, width, height)：设置捕获区域（ADB 为 no-op）
    - attach_to_window(title_keyword)：附着到指定窗口（ADB 为 no-op）
    """

    @abstractmethod
    def capture(self, timeout: float = 5.0) -> np.ndarray | None:
        """截取一帧屏幕/设备画面

        Args:
            timeout: 超时秒数（默认 5s）

        Returns:
            BGR numpy 数组，失败返回 None
        """

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

    def set_capture_region(self, left: int, top: int, width: int, height: int):
        """设置捕获区域

        桌面 mss 子类覆盖实现；ADB 子类为 no-op（设备全屏截图）。
        """
        pass

    def attach_to_window(self, title_keyword: str) -> bool:
        """通过窗口标题关键词附着到指定窗口

        桌面 mss 子类覆盖实现；ADB 子类为 no-op 返回 False。
        """
        return False
