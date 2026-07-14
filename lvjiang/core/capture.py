"""屏幕捕获模块 - 基于 mss，支持投屏窗口定位"""

import numpy as np
import mss
import mss.tools
from loguru import logger


class ScreenCapture:
    """屏幕捕获器"""

    def __init__(self):
        self._sct = mss.mss()
        self._monitor = None  # 目标投屏窗口的捕获区域

    def list_monitors(self) -> list[dict]:
        """列出所有可用显示器/捕获区域"""
        return [dict(m) for m in self._sct.monitors]

    def set_capture_region(self, left: int, top: int, width: int, height: int):
        """设置捕获区域（用于定位投屏窗口）"""
        self._monitor = {
            "left": left,
            "top": top,
            "width": width,
            "height": height,
        }
        logger.info(f"设置捕获区域: left={left}, top={top}, width={width}, height={height}")

    def capture(self) -> np.ndarray | None:
        """
        截取屏幕，返回 numpy 数组（BGR 格式，OpenCV 兼容）
        如果设置了 _monitor 则截取指定区域，否则截取全屏
        """
        try:
            if self._monitor:
                screenshot = self._sct.grab(self._monitor)
            else:
                # 默认截取主显示器
                screenshot = self._sct.grab(self._sct.monitors[1])

            # 转换为 numpy 数组 (BGRA)
            img = np.array(screenshot)
            # BGRA -> BGR
            return img[:, :, :3]
        except Exception as e:
            logger.error(f"截屏失败: {e}")
            return None

    def capture_to_file(self, path: str) -> bool:
        """截屏并保存为 PNG 文件"""
        img = self.capture()
        if img is None:
            return False
        try:
            mss.tools.to_png(img.tobytes(), (img.shape[1], img.shape[0]), output=path)
            logger.info(f"截图已保存: {path}")
            return True
        except Exception as e:
            logger.error(f"保存截图失败: {e}")
            return False

    def find_window_by_title(self, title_keyword: str) -> dict | None:
        """
        通过窗口标题关键词查找投屏窗口
        注意：mss 本身不支持按窗口标题查找，这里用 pyautogui 辅助
        返回窗口的位置信息 dict 或 None
        """
        try:
            import pyautogui
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            EnumWindows = user32.EnumWindows
            GetWindowTextW = user32.GetWindowTextW
            GetWindowRect = user32.GetWindowRect
            IsWindowVisible = user32.IsWindowVisible

            results = []

            def _enum_callback(hwnd, _):
                if IsWindowVisible(hwnd):
                    length = GetWindowTextW(hwnd)
                    if length > 0:
                        buf = ctypes.create_unicode_buffer(length + 1)
                        GetWindowTextW(hwnd, buf, length + 1)
                        title = buf.value
                        if title_keyword.lower() in title.lower():
                            rect = wintypes.RECT()
                            GetWindowRect(hwnd, ctypes.byref(rect))
                            results.append({
                                "title": title,
                                "hwnd": hwnd,
                                "left": rect.left,
                                "top": rect.top,
                                "width": rect.right - rect.left,
                                "height": rect.bottom - rect.top,
                            })

            EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))(_enum_callback), None)

            if results:
                logger.info(f"找到 {len(results)} 个匹配窗口: {[r['title'] for r in results]}")
                return results[0]
            else:
                logger.warning(f"未找到包含 '{title_keyword}' 的窗口")
                return None

        except Exception as e:
            logger.error(f"查找窗口失败: {e}")
            return None

    def attach_to_window(self, title_keyword: str) -> bool:
        """
        附着到指定投屏窗口（通过标题查找并设置捕获区域）
        """
        window = self.find_window_by_title(title_keyword)
        if window:
            self.set_capture_region(
                window["left"], window["top"],
                window["width"], window["height"]
            )
            return True
        return False


def list_visible_windows() -> list[dict]:
    """
    列出所有可见窗口（Win32 API）
    返回 list[dict]，每个 dict 包含 title, hwnd, left, top, width, height
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    results = []

    def _callback(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
                # 过滤掉无标题窗口和系统窗口
                if title.strip():
                    rect = wintypes.RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(rect))
                    w = rect.right - rect.left
                    h = rect.bottom - rect.top
                    # 过滤掉太小的窗口（工具栏、托盘等）
                    if w > 100 and h > 100:
                        results.append({
                            "title": title,
                            "hwnd": hwnd,
                            "left": rect.left,
                            "top": rect.top,
                            "width": w,
                            "height": h,
                        })
        return True

    CALLBACK = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
    user32.EnumWindows(CALLBACK(_callback), None)

    logger.debug(f"枚举到 {len(results)} 个可见窗口")
    return results
