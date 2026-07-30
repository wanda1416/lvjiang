"""桌面端截图后端 - 基于 mss，支持投屏窗口定位

使用一个持久化的专用工作线程执行截屏，避免每次 capture() 都创建新线程。
旧实现每次截屏创建 daemon 线程 + 线程本地 mss 实例，导致：
- 线程栈内存（~1MB/次）不断累积
- mss 缓存的 GDI 设备上下文（srcdc/memdc）随线程泄漏
- 超时线程被遗弃，GDI 句柄永远无法回收
新实现：单个工作线程拥有唯一 mss 实例，通过任务队列接收截屏请求。
"""

import queue
import threading
import time

import mss
import mss.tools
import numpy as np
from loguru import logger

from ..capture_base import CaptureBackend


class DesktopCapture(CaptureBackend):
    """桌面端截图后端（mss + 工作线程）

    继承 CaptureBackend，提供桌面窗口截图能力。
    """

    # 连续超时多少次后认为工作线程死锁，触发重建
    _WORKER_DEADLOCK_THRESHOLD = 3

    def __init__(self):
        self._monitor = None  # 目标投屏窗口的捕获区域
        self._main_sct = None  # 主线程 mss 实例（仅用于 list_monitors / get_capture_size）
        self._task_queue: queue.Queue = queue.Queue(maxsize=2)
        self._consecutive_timeouts = 0  # 连续超时计数
        self._worker = threading.Thread(target=self._worker_loop, daemon=True, name="DesktopCapture")
        self._worker.start()

    def _worker_loop(self):
        """专用工作线程：拥有唯一 mss 实例，从任务队列取请求执行截屏"""
        try:
            sct = mss.mss()
        except Exception as e:
            logger.error(f"mss 初始化失败: {e}")
            return

        try:
            while True:
                task = self._task_queue.get()
                if task is None:  # 关闭信号
                    break
                monitor, result_holder = task
                try:
                    target = monitor if monitor is not None else sct.monitors[1]
                    screenshot = sct.grab(target)
                    img = np.array(screenshot)
                    result_holder[0] = img[:, :, :3]
                except Exception as e:
                    result_holder[0] = e
        finally:
            try:
                sct.close()
            except Exception:
                pass

    @property
    def _sct(self):
        """主线程 mss 实例（惰性创建，仅用于 list_monitors / get_capture_size）"""
        if self._main_sct is None:
            self._main_sct = mss.mss()
        return self._main_sct

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

    def get_capture_size(self) -> tuple[int, int]:
        """返回当前捕获区域的宽高（像素），不截屏

        Returns:
            (width, height)
        """
        if self._monitor:
            return self._monitor["width"], self._monitor["height"]
        # 未设置捕获区域时，取主显示器分辨率
        mon = self._sct.monitors[1]
        return mon["width"], mon["height"]

    def _restart_worker(self):
        """重建工作线程和任务队列（旧线程死锁时调用）

        旧的死锁线程作为 daemon 线程会被遗弃，进程退出时自动回收。
        新建队列确保新线程不会收到旧任务。
        """
        logger.warning("截屏工作线程疑似死锁，正在重建...")
        self._task_queue = queue.Queue(maxsize=2)
        self._consecutive_timeouts = 0
        self._worker = threading.Thread(
            target=self._worker_loop, daemon=True,
            name=f"DesktopCapture-{time.monotonic():.0f}",
        )
        self._worker.start()
        logger.info("截屏工作线程已重建")

    def capture(self, timeout: float = 5.0) -> np.ndarray | None:
        """截取屏幕，返回 numpy 数组（BGR 格式，OpenCV 兼容）

        如果设置了 _monitor 则截取指定区域，否则截取全屏。

        Args:
            timeout: 超时秒数（默认 5s）。mss.grab 底层调用 Win32 GDI，
                     可能因显示状态变化而永久阻塞，需要超时保护。
        """
        if not self._worker.is_alive():
            logger.warning("截屏工作线程已退出，尝试重建")
            self._restart_worker()

        result_holder: list = [None]

        try:
            self._task_queue.put((self._monitor, result_holder), timeout=1.0)
        except queue.Full:
            logger.error("截屏队列已满（工作线程可能阻塞），跳过本次捕获")
            self._consecutive_timeouts += 1
            if self._consecutive_timeouts >= self._WORKER_DEADLOCK_THRESHOLD:
                self._restart_worker()
            return None

        # 等待工作线程填充结果
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if result_holder[0] is not None:
                break
            time.sleep(0.005)

        if result_holder[0] is None:
            logger.error(f"截屏超时（{timeout}s），跳过本次捕获")
            self._consecutive_timeouts += 1
            if self._consecutive_timeouts >= self._WORKER_DEADLOCK_THRESHOLD:
                self._restart_worker()
            return None

        if isinstance(result_holder[0], Exception):
            logger.error(f"截屏失败: {result_holder[0]}")
            self._consecutive_timeouts += 1
            if self._consecutive_timeouts >= self._WORKER_DEADLOCK_THRESHOLD:
                self._restart_worker()
            return None

        # 成功：重置连续超时计数
        self._consecutive_timeouts = 0
        return result_holder[0]

    def capture_to_file(self, path: str) -> bool:
        """截屏并保存为 PNG 文件（使用 mss.tools.to_png）"""
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
        """通过窗口标题关键词查找投屏窗口

        使用 ctypes + Win32 API (EnumWindows / GetWindowTextW)
        返回窗口的位置信息 dict 或 None
        """
        try:
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
        """附着到指定投屏窗口（通过标题查找并设置捕获区域）"""
        window = self.find_window_by_title(title_keyword)
        if window:
            self.set_capture_region(
                window["left"], window["top"],
                window["width"], window["height"]
            )
            return True
        return False
