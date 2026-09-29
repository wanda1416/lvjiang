"""桌面端后台截图后端 — Windows Graphics Capture

与 DesktopCapture（mss 抓屏幕矩形）的区别只有一条，但决定了能不能后台跑：
**WGC 抓的是窗口自己的合成内容，窗口被别的窗口盖住照样拿得到。** mss 抓的是屏幕上
那块矩形，谁盖在上面就抓到谁。

不能解决最小化：窗口最小化后没有被合成的客户区表面，WGC 随之停帧。也就是说这条路
的上限是「被遮挡也能跑」，不是「最小化挂后台」。

取帧模型与 AndroidStreamCapture 同构，都是推送式：后台线程收帧 → 原子更新最新帧，
``capture()`` 直接返回副本，不做 IO。

── 几何对齐（这块最容易出错，改动前务必读完）──────────────────────

WGC 交付的帧是窗口**可见边界**（DWM ``DWMWA_EXTENDED_FRAME_BOUNDS``），而 mss 抓的是
``GetWindowRect``——后者在 Win10 上两侧各多出约 8px 不可见调整边框。两者口径不同，
直接把 WGC 帧当 mss 图用，会让所有按窗口矩形标定过的布局整体偏移几个像素。

所以这里把帧按两个矩形的差值贴回窗口矩形大小的画布上，输出与 mss 那张图逐像素同构，
既有布局不用重标。多出来的边框区域填黑：那本来就是窗口背后的内容，不会有任何识别区
落在上面。若某些窗口两个矩形恰好一致，偏移为 0，贴图退化成直接拷贝。
"""

import threading
import time

import numpy as np
from loguru import logger

from ...i18n import tr
from ..capture_base import CaptureBackend
from .win32_util import get_window_rect, get_window_visible_rect


def wgc_available() -> tuple[bool, str]:
    """WGC 后端是否可用，返回 ``(可用, 不可用原因)``。

    依赖是 Windows 专属的可选件，未装时给出可执行的提示而不是抛 ImportError。
    """
    import sys
    if sys.platform != "win32":
        return False, tr("后台截图仅支持 Windows")
    try:
        import windows_capture  # noqa: F401
    except ImportError:
        return False, tr("缺少 windows-capture 依赖，请重新安装或更新程序")
    except Exception as exc:  # 加载原生扩展失败（缺 VC 运行时等）
        return False, tr("后台截图组件加载失败: ") + str(exc)
    return True, ""


class WgcCapture(CaptureBackend):
    """基于 Windows Graphics Capture 的窗口截图后端（被遮挡仍可用）"""

    #: 首帧等待预算：WGC 会话建立 + DWM 交付第一帧
    _FIRST_FRAME_TIMEOUT = 5.0

    def __init__(self):
        self._hwnd: int | None = None
        self._control = None
        self._lock = threading.Lock()
        self._latest_frame: np.ndarray | None = None
        self._frame_event = threading.Event()
        #: 期望输出的窗口矩形 (left, top, width, height)，由 set_capture_region 写入
        self._region: tuple[int, int, int, int] | None = None
        self._geometry_logged = False
        self._closed = False

    # ─── 会话生命周期 ──────────────────────────────────────

    def attach_hwnd(self, hwnd: int) -> bool:
        """对指定窗口建立 WGC 会话，等到第一帧才算成功。

        重复对同一窗口调用是幂等的；换窗口会先停掉旧会话。
        """
        if self._hwnd == hwnd and self._control is not None:
            return True
        self.stop()

        ok, reason = wgc_available()
        if not ok:
            logger.error(f"后台截图不可用: {reason}")
            return False

        from windows_capture import WindowsCapture

        try:
            session = WindowsCapture(
                cursor_capture=False,   # 光标进画面会污染 OCR
                draw_border=False,      # 捕获黄框；部分系统版本不允许关闭，失败也不影响取帧
                window_hwnd=hwnd,
            )
        except Exception as exc:
            logger.error(f"创建 WGC 会话失败 (hwnd={hwnd}): {exc}")
            return False

        @session.event
        def on_frame_arrived(frame, capture_control):  # noqa: ARG001
            # frame_buffer 是原生映射内存的零拷贝视图，回调返回后即失效，必须拷出来
            try:
                bgr = np.array(frame.frame_buffer[:, :, :3], copy=True)
            except Exception as exc:
                logger.debug(f"WGC 帧转换失败: {exc}")
                return
            with self._lock:
                self._latest_frame = bgr
            self._frame_event.set()

        @session.event
        def on_closed():
            logger.info("WGC 会话已关闭（窗口关闭或捕获被系统终止）")
            self._frame_event.set()

        try:
            self._control = session.start_free_threaded()
        except Exception as exc:
            logger.error(f"启动 WGC 会话失败 (hwnd={hwnd}): {exc}")
            self._control = None
            return False

        self._hwnd = hwnd
        self._frame_event.clear()
        if not self._frame_event.wait(self._FIRST_FRAME_TIMEOUT):
            logger.error(
                f"WGC 首帧等待超时（{self._FIRST_FRAME_TIMEOUT}s）：窗口可能已最小化——"
                f"最小化状态下 WGC 不产帧，后台截图只能解决遮挡")
            self.stop()
            return False
        logger.info(f"后台截图已启用 (hwnd={hwnd})")
        return True

    def attach_to_window(self, title_keyword: str) -> bool:
        """按标题关键字定位并建立会话（CaptureBackend 契约要求）"""
        from .win32_util import list_visible_windows
        for w in list_visible_windows():
            if title_keyword in w.get("title", ""):
                self.set_capture_region(w["left"], w["top"], w["width"], w["height"])
                return self.attach_hwnd(w["hwnd"])
        logger.error(f"未找到标题含 {title_keyword!r} 的窗口")
        return False

    def stop(self):
        """停止会话并释放最新帧"""
        control, self._control = self._control, None
        if control is not None:
            try:
                control.stop()
            except Exception as exc:
                logger.debug(f"停止 WGC 会话时报错（忽略）: {exc}")
        self._hwnd = None
        with self._lock:
            self._latest_frame = None
        self._frame_event.clear()
        self._geometry_logged = False

    # ─── 取帧 ──────────────────────────────────────────────

    def set_capture_region(self, left: int, top: int, width: int, height: int):
        """记录目标窗口矩形（屏幕物理坐标），输出帧按它对齐。

        与 DesktopCapture 同签名：上层每次截图前都会用 GetWindowRect 的结果调一次，
        这里只记下来，真正的裁剪/补边在 ``capture()`` 里做。
        """
        self._region = (int(left), int(top), int(width), int(height))

    def capture(self, timeout: float = 5.0) -> np.ndarray | None:
        """返回最新一帧（BGR），按窗口矩形对齐；无帧可用返回 ``None``"""
        if self._control is None:
            logger.error("后台截图未启动，请先定位窗口")
            return None
        deadline = time.time() + timeout
        while True:
            with self._lock:
                frame = self._latest_frame
            if frame is not None:
                return self._align_to_region(frame)
            if time.time() >= deadline:
                logger.error(f"后台截图取帧超时（{timeout}s）")
                return None
            self._frame_event.wait(min(0.2, max(0.0, deadline - time.time())))

    def get_capture_size(self) -> tuple[int, int]:
        """输出图像尺寸 ``(width, height)``，与 ``capture()`` 返回的一致"""
        if self._region is not None:
            return self._region[2], self._region[3]
        with self._lock:
            frame = self._latest_frame
        if frame is None:
            return 0, 0
        return frame.shape[1], frame.shape[0]

    # ─── 几何对齐 ──────────────────────────────────────────

    def _align_to_region(self, frame: np.ndarray) -> np.ndarray:
        """把 WGC 帧贴回窗口矩形尺寸，使输出与 mss 那张图逐像素同构"""
        if self._region is None or self._hwnd is None:
            return frame
        _, _, region_w, region_h = self._region
        frame_h, frame_w = frame.shape[:2]
        if (frame_w, frame_h) == (region_w, region_h):
            return frame

        offset_x, offset_y = self._frame_offset()
        canvas = np.zeros((region_h, region_w, 3), dtype=frame.dtype)
        # 双向取交集：窗口在缩放或刚改尺寸时两边都可能超出对方
        src_x = max(0, -offset_x)
        src_y = max(0, -offset_y)
        dst_x = max(0, offset_x)
        dst_y = max(0, offset_y)
        copy_w = min(frame_w - src_x, region_w - dst_x)
        copy_h = min(frame_h - src_y, region_h - dst_y)
        if copy_w <= 0 or copy_h <= 0:
            logger.warning(
                f"后台截图帧与窗口矩形无重叠（帧 {frame_w}x{frame_h}，"
                f"窗口 {region_w}x{region_h}，偏移 {offset_x},{offset_y}），返回原帧")
            return frame
        canvas[dst_y:dst_y + copy_h, dst_x:dst_x + copy_w] = \
            frame[src_y:src_y + copy_h, src_x:src_x + copy_w]
        return canvas

    def _frame_offset(self) -> tuple[int, int]:
        """WGC 帧左上角相对窗口矩形左上角的偏移（像素）

        帧对应 DWM 可见边界，窗口矩形来自 GetWindowRect，两者之差即偏移。
        取不到可见边界时退回 0，此时输出等价于把帧按左上角对齐。
        """
        if self._hwnd is None or self._region is None:
            return 0, 0
        visible = get_window_visible_rect(self._hwnd)
        if visible is None:
            return 0, 0
        window = get_window_rect(self._hwnd) or self._region
        offset = (visible[0] - window[0], visible[1] - window[1])
        if not self._geometry_logged:
            self._geometry_logged = True
            logger.info(
                f"后台截图几何: 窗口矩形 {window[2]}x{window[3]} @({window[0]},{window[1]})，"
                f"可见边界 {visible[2]}x{visible[3]} @({visible[0]},{visible[1]})，"
                f"帧偏移 {offset}")
        return offset
