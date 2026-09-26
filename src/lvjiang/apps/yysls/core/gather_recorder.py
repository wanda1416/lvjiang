"""采集选点监听：只捕获游戏前台窗口内的地图点击，不生成键盘脚本。"""
from __future__ import annotations

import ctypes
import sys
import threading
import time
from copy import deepcopy
from typing import Callable

from .gather import GatherClickBuffer, crop_region, viewport_signature


class GatherInputRecorder:
    def __init__(self, capture, layout, window: dict, *,
                 changed: Callable[[], None], failed: Callable[[str], None]):
        self.capture = capture
        self.layout = deepcopy(layout)
        self.window = dict(window)
        self.changed = changed
        self.failed = failed
        self.buffer = GatherClickBuffer()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.listener = None
        self.thread: threading.Thread | None = None
        self._geometry: tuple[int, int, int, int] | None = None

    def _window_geometry(self) -> tuple[int, int, int, int]:
        from ctypes import wintypes
        rect = wintypes.RECT()
        hwnd = ctypes.c_void_p(int(self.window.get("hwnd", 0)))
        if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise ValueError("游戏窗口已失效，请重新定位")
        return rect.left, rect.top, rect.right, rect.bottom

    def foreground(self) -> bool:
        if sys.platform != "win32":
            return False
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = wintypes.HWND
        return int(user32.GetForegroundWindow() or 0) == int(self.window.get("hwnd", 0))

    def start(self) -> None:
        if sys.platform != "win32":
            raise ValueError("实验性采集选点录制目前支持 Windows 桌面")
        self._geometry = self._window_geometry()
        from ....core.pynput_patch import install
        install()
        from pynput import mouse
        self.listener = mouse.Listener(on_click=self._on_click)
        self.listener.start()
        self.thread = threading.Thread(target=self._capture_loop, daemon=True, name="gather-preview")
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.listener is not None:
            self.listener.stop()
            self.listener = None
        if self.thread is not None:
            self.thread.join(timeout=2)
            self.thread = None

    def _capture_loop(self) -> None:
        try:
            while not self.stop_event.is_set():
                if self._window_geometry() != self._geometry:
                    raise ValueError("游戏窗口已移动或缩放，请重新定位窗口后开始选点")
                if self.foreground():
                    frame = self.capture.capture(timeout=0.5)
                    if frame is not None:
                        signature = viewport_signature(crop_region(frame, self.layout, "map_view"))
                        with self.lock:
                            self.buffer.update_frame(time.monotonic(), signature)
                self.stop_event.wait(0.15)
        except Exception as exc:
            if not self.stop_event.is_set():
                self.failed(str(exc))

    def _on_click(self, x, y, button, pressed) -> None:
        if self.stop_event.is_set() or not self.foreground() or str(button) != "Button.left":
            return
        width, height = self.capture.get_capture_size()
        canvas = self.layout.get_canvas()
        if not width or not height:
            return
        rx = ((x - self.window["left"]) / width - canvas.x_ratio) / canvas.w_ratio
        ry = ((y - self.window["top"]) / height - canvas.y_ratio) / canvas.h_ratio
        area = next((r for r in self.layout.get_scene_regions("map_gather") if r.key == "map_area"), None)
        if area is None:
            return
        inside = (area.x_ratio <= rx <= area.x_ratio + area.w_ratio
                  and area.y_ratio <= ry <= area.y_ratio + area.h_ratio)
        with self.lock:
            if pressed:
                self.buffer.pending = None
                self.buffer.down = None
                if inside:
                    self.buffer.press(rx, ry, time.monotonic())
            elif inside:
                self.buffer.release(rx, ry)
            else:
                self.buffer.down = None
                self.buffer.pending = None
        self.changed()

    def confirm(self):
        with self.lock:
            return self.buffer.confirm()
