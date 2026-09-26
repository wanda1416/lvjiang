"""采集选点监听：使用主程序连接状态捕获目标区域点击，不生成键盘脚本。"""
from __future__ import annotations

import threading
import time
from copy import deepcopy
from typing import Callable

from .gather import GatherClickBuffer, crop_region, viewport_signature


class GatherInputRecorder:
    def __init__(self, capture, layout, window: dict, *,
                 connected: Callable[[], bool], changed: Callable[[], None],
                 failed: Callable[[str], None]):
        self.capture = capture
        self.layout = deepcopy(layout)
        self.window = dict(window)
        self.connected = connected
        self.changed = changed
        self.failed = failed
        self.buffer = GatherClickBuffer()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.listener = None
        self.thread: threading.Thread | None = None
    def start(self) -> None:
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
                if not self.connected():
                    raise ValueError("游戏连接已断开，请重新连接后开始选点")
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
        if self.stop_event.is_set() or not self.connected() or str(button) != "Button.left":
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
