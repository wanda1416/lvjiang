"""采集选点监听：使用主程序连接状态捕获目标区域点击，不生成键盘脚本。"""
from __future__ import annotations

import threading
import time
from copy import deepcopy
from typing import Callable

from .gather import GatherClickBuffer, crop_region, viewport_signature


class GatherInputRecorder:
    def __init__(self, capture, layout, window: dict, *,
                 connected: Callable[[], bool],
                 failed: Callable[[str], None], cursor_position: Callable[[], tuple[int, int]] | None = None):
        self.capture = capture
        self.layout = deepcopy(layout)
        self.window = dict(window)
        self.connected = connected
        self.failed = failed
        self.cursor_position = cursor_position or self._cursor_position
        self.buffer = GatherClickBuffer()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    @staticmethod
    def _cursor_position() -> tuple[int, int]:
        from ....core.pynput_patch import install
        install()
        from pynput import mouse
        x, y = mouse.Controller().position
        return int(x), int(y)

    def start(self) -> None:
        self.thread = threading.Thread(target=self._capture_loop, daemon=True, name="gather-preview")
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
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

    def mark_current(self):
        if self.stop_event.is_set() or not self.connected():
            raise ValueError("游戏连接已断开，请重新连接后开始选点")
        x, y = self.cursor_position()
        width, height = self.capture.get_capture_size()
        canvas = self.layout.get_canvas()
        if not width or not height:
            raise ValueError("无法取得游戏画布尺寸")
        rx = ((x - self.window["left"]) / width - canvas.x_ratio) / canvas.w_ratio
        ry = ((y - self.window["top"]) / height - canvas.y_ratio) / canvas.h_ratio
        area = next((r for r in self.layout.get_scene_regions("map_gather") if r.key == "map_area"), None)
        if area is None or area.disabled or not area.has_position:
            raise ValueError("采集场景缺少可用标定：map_area，请在场景编辑器中标定")
        inside = (area.x_ratio <= rx <= area.x_ratio + area.w_ratio
                  and area.y_ratio <= ry <= area.y_ratio + area.h_ratio)
        if not inside:
            raise ValueError("鼠标不在地图选点区域，请悬停到采集物图标后重试")
        with self.lock:
            step = self.buffer.mark(rx, ry, time.monotonic())
        return step
