"""采集选点监听：F1 读取当前鼠标位置并生成领域动作。"""
from __future__ import annotations

import threading
from copy import deepcopy
from typing import Callable

from .gather import GatherClickBuffer


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

    @staticmethod
    def _cursor_position() -> tuple[int, int]:
        from ....core.pynput_patch import install
        install()
        from pynput import mouse
        x, y = mouse.Controller().position
        return int(x), int(y)

    def start(self) -> None:
        self.stop_event.clear()

    def stop(self) -> None:
        self.stop_event.set()

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
            step = self.buffer.mark(rx, ry)
        return step
