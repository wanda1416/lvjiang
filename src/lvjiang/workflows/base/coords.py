"""归一化坐标 → 屏幕绝对坐标换算"""

import math
import random

from loguru import logger

from ...core.scene_registry import Point, Region


class _CoordMixin:
    """region / point / 画布归一化坐标到屏幕坐标的换算"""

    def _region_to_screen(self, region: Region, jitter: bool = True) -> tuple[int | None, int | None]:
        """区域坐标 → 屏幕坐标"""
        w, h = self._capture.get_capture_size()
        canvas = self._layout.get_canvas()

        canvas_x = canvas.x_ratio * w
        canvas_y = canvas.y_ratio * h
        canvas_w = canvas.w_ratio * w
        canvas_h = canvas.h_ratio * h

        cx = canvas_x + (region.x_ratio + region.w_ratio / 2) * canvas_w
        cy = canvas_y + (region.y_ratio + region.h_ratio / 2) * canvas_h

        if jitter:
            jitter_ratio = self._delay.region_jitter_ratio
            region_w = region.w_ratio * canvas_w
            region_h = region.h_ratio * canvas_h
            cx += region_w * random.uniform(-jitter_ratio, jitter_ratio)
            cy += region_h * random.uniform(-jitter_ratio, jitter_ratio)

        return int(self._window_left + cx), int(self._window_top + cy)

    def _point_to_screen(self, point: Point) -> tuple[int | None, int | None]:
        """point 中心 → 屏幕坐标（带半径内随机偏移）"""
        size = self._capture.get_capture_size()
        if size == (0, 0):
            logger.error("无法获取截屏尺寸")
            return None, None
        w, h = size
        canvas = self._layout.get_canvas()
        canvas_x = canvas.x_ratio * w
        canvas_y = canvas.y_ratio * h
        canvas_w = canvas.w_ratio * w
        canvas_h = canvas.h_ratio * h
        cx = canvas_x + point.cx_ratio * canvas_w
        cy = canvas_y + point.cy_ratio * canvas_h
        # 半径内随机偏移
        r = point.r_ratio * min(canvas_w, canvas_h)
        angle = random.uniform(0, 2 * math.pi)
        dist = random.uniform(0, r)
        cx += dist * math.cos(angle)
        cy += dist * math.sin(angle)
        return int(self._window_left + cx), int(self._window_top + cy)

    def _ratio_to_screen(self, cx_ratio: float, cy_ratio: float) -> tuple[int | None, int | None]:
        """画布内归一化坐标 → 屏幕坐标"""
        size = self._capture.get_capture_size()
        if size == (0, 0):
            logger.error("无法获取截屏尺寸")
            return None, None
        w, h = size
        canvas = self._layout.get_canvas()
        sx = canvas.x_ratio + cx_ratio * canvas.w_ratio
        sy = canvas.y_ratio + cy_ratio * canvas.h_ratio
        return int(self._window_left + sx * w), int(self._window_top + sy * h)
