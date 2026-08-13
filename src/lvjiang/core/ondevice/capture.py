"""设备端截图后端

两条通道：
  A11yCapture   主通道，无障碍服务 takeScreenshot，拿到 RGBA 裸字节
  ShellCapture  可选通道，screencap -p 经 Shizuku，不受截图节流限制

主通道选无障碍而不是 Shizuku：后者必须由 adb 引导启动，手机重启一次就失效，
对普通用户不成立。两边都返回同一形状的 BGR numpy，上层无需区分。

不用 loguru：设备端依赖里没有它（见 android/app/build.gradle.kts 的 pip 块）。
错误通过返回 None + print 暴露。
"""

import time

import numpy as np

from ...i18n import tr
from ..capture_base import CaptureBackend
from . import a11y, shell


class A11yCapture(CaptureBackend):
    """基于无障碍 takeScreenshot 的截图后端（主通道）

    比 screencap 快一个量级：拿到的是 RGBA 裸字节，numpy 直接 reshape，
    省掉「PNG 压缩 → imdecode 解压」这一对纯浪费的往返。

    代价是 takeScreenshot 有节流（数百毫秒级最小间隔），连续调用过快会失败。
    调律场景是「操作一步 → 截一张」的秒级节奏，够用；真需高帧率再上
    MediaProjection（它底层就是 scrcpy 那套 VirtualDisplay，不限频）。
    """

    #: 节流退避：takeScreenshot 的最小间隔是数百毫秒级，失败后干等一下大多能成
    _RETRY_DELAY = 0.4
    _MAX_ATTEMPTS = 3

    def __init__(self):
        self._size: tuple[int, int] | None = None

    def capture(self, timeout: float = 10.0) -> np.ndarray | None:
        got = self._grab(timeout)
        if got is None:
            return None

        width, height, data = got
        expect = width * height * 4
        if len(data) != expect:
            print(f"[A11yCapture] 字节数不对：{len(data)} != {width}x{height}x4={expect}")
            return None

        import cv2

        rgba = np.frombuffer(data, dtype=np.uint8).reshape(height, width, 4)
        # Bitmap.copyPixelsToBuffer 对 ARGB_8888 写出的内存序列实际是 R,G,B,A
        img = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR)
        self._size = (width, height)
        return img

    def _grab(self, timeout: float):
        """取一帧 RGBA，失败时重试；返回 (宽, 高, 字节) 或 None

        分两种失败区别对待：无障碍服务掉线是硬故障（重试没有意义，直接报清楚，
        由上层引导用户去开开关）；返回 None 但服务在线则大概率是截图节流，
        退避一下再试。长任务里连续截图撞上节流是常态，不重试就会中途莫名失败。
        """
        for attempt in range(1, self._MAX_ATTEMPTS + 1):
            if not a11y.is_ready():
                print(tr("[A11yCapture] 无障碍服务未连接（开关未开或被系统关掉），请重新开启"))
                return None

            try:
                got = a11y.screenshot_rgba(int(timeout * 1000))
            except Exception as e:
                print(f"[A11yCapture] takeScreenshot 调用异常: {e}")
                return None

            if got is not None:
                return got

            if attempt < self._MAX_ATTEMPTS:
                print(f"[A11yCapture] 截图失败（疑似节流），{self._RETRY_DELAY}s 后重试 {attempt}/{self._MAX_ATTEMPTS - 1}")
                time.sleep(self._RETRY_DELAY)

        print(f"[A11yCapture] 截图连续 {self._MAX_ATTEMPTS} 次失败")
        return None

    def get_capture_size(self) -> tuple[int, int]:
        if self._size is None:
            self.capture()
        return self._size or (0, 0)


class ShellCapture(CaptureBackend):
    """基于 ShellBridge screencap 的截图后端（可选通道）

    与 PC 端 AdbCapture 是同一条命令（screencap -p）、同一套解码逻辑，区别只在于
    命令由谁下发：PC 是 adb 子进程，设备端是 ShellBridge 直连 shell uid 进程。
    """

    def __init__(self):
        self._size: tuple[int, int] | None = None

    def capture(self, timeout: float = 10.0) -> np.ndarray | None:
        """截一帧并解码为 BGR numpy

        Args:
            timeout: 保留以对齐 CaptureBackend 签名。实际超时由 Binder 调用本身
                决定（ShellBridge.exec 是同步阻塞），这里无法单独控制。
        """
        try:
            png = shell.screencap_png()
        except Exception as e:
            print(f"[ShellCapture] screencap 调用异常: {e}")
            return None

        if not png:
            print(tr("[ShellCapture] screencap 返回空数据（Shizuku 未授权？）"))
            return None
        # 通道未就绪时 ShellBridge 会回一段 [stderr]... 文本而不是 PNG，
        # 直接送进 imdecode 只会得到一个含义不明的 None
        if png[:8] != b"\x89PNG\r\n\x1a\n":
            print(f"[ShellCapture] 返回的不是 PNG：{png[:80]!r}")
            return None

        import cv2

        img = cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            print(f"[ShellCapture] PNG 解码失败（{len(png)} 字节）")
            return None

        # 不做旋转：screencap 的输出方向与 input tap 的坐标系一致，
        # 这与 PC 端 AdbCapture 的结论相同（同一个 screencap 实现）。
        h, w = img.shape[:2]
        self._size = (w, h)
        return img

    def get_capture_size(self) -> tuple[int, int]:
        """实际截图尺寸（width, height）

        以截图为准而非 wm size：游戏强制横屏时二者方向相反，用错基准会让
        归一化坐标整体偏移。
        """
        if self._size is None:
            self.capture()
        return self._size or (0, 0)

    # set_capture_region / attach_to_window 沿用 CaptureBackend 默认 no-op
