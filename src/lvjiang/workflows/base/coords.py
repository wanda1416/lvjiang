"""归一化坐标 → 屏幕绝对坐标换算

拿不到截屏尺寸意味着截屏后端不可用，此时任何坐标都是错的，
直接抛错而不是返回 None 让调用方静默跳过。
"""

import math
import random

from ...core.layout_models import Point, Region, effective_click_rect
from ...i18n import tr


class _CoordMixin:
    """region / point / 画布归一化坐标到屏幕坐标的换算"""

    def _capture_size(self) -> tuple[int, int]:
        """取截屏尺寸，拿不到就抛错"""
        size = self._capture.get_capture_size()
        if size == (0, 0):
            raise ValueError(tr("无法获取截屏尺寸，无法换算屏幕坐标（检查截屏后端）"))
        return size

    def _region_to_screen(self, region: Region, jitter: bool = True) -> tuple[int, int]:
        """区域坐标 → 屏幕坐标（落点取自区域的 click_rect，见 effective_click_rect）

        jitter 只决定「框内随机取点」还是「取框中心」；点在区域的哪一块由
        click_rect 决定。两者正交，不再像旧实现那样挤在一个开关里。
        """
        w, h = self._capture_size()
        canvas = self._layout.get_canvas()

        canvas_x = canvas.x_ratio * w
        canvas_y = canvas.y_ratio * h
        canvas_w = canvas.w_ratio * w
        canvas_h = canvas.h_ratio * h

        rx, ry, rw, rh = effective_click_rect(
            region, self._input_sim.region_jitter_ratio)
        # 相对区域 → 画布归一化
        x0 = region.x_ratio + rx * region.w_ratio
        y0 = region.y_ratio + ry * region.h_ratio
        bw = rw * region.w_ratio
        bh = rh * region.h_ratio

        if jitter:
            fx, fy = random.uniform(0, bw), random.uniform(0, bh)
        else:
            fx, fy = bw / 2, bh / 2

        cx = canvas_x + (x0 + fx) * canvas_w
        cy = canvas_y + (y0 + fy) * canvas_h
        return int(self._window_left + cx), int(self._window_top + cy)

    def _point_to_screen(self, point: Point, jitter: bool = True) -> tuple[int, int]:
        """point 中心 → 屏幕坐标（默认带半径内随机偏移）"""
        return self._ratio_point_to_screen(
            point.cx_ratio, point.cy_ratio, point.r_ratio, jitter=jitter)

    def _ratio_point_to_screen(
        self,
        cx_ratio: float,
        cy_ratio: float,
        r_ratio: float,
        *,
        jitter: bool = True,
        clamp_to_canvas: bool = False,
    ) -> tuple[int, int]:
        """画布归一化圆心 + 半径 → 屏幕坐标。

        ``jitter`` 时在半径内随机取点；``clamp_to_canvas`` 在抖动完成后把
        最终落点限制到画布边界，供不允许越界的拖拽路径使用。
        """
        w, h = self._capture_size()
        canvas = self._layout.get_canvas()
        canvas_x = canvas.x_ratio * w
        canvas_y = canvas.y_ratio * h
        canvas_w = canvas.w_ratio * w
        canvas_h = canvas.h_ratio * h
        cx = canvas_x + cx_ratio * canvas_w
        cy = canvas_y + cy_ratio * canvas_h
        if jitter:
            r = r_ratio * min(canvas_w, canvas_h)
            angle = random.uniform(0, 2 * math.pi)
            dist = random.uniform(0, r)
            cx += dist * math.cos(angle)
            cy += dist * math.sin(angle)
        if clamp_to_canvas:
            cx = min(max(cx, canvas_x), canvas_x + canvas_w)
            cy = min(max(cy, canvas_y), canvas_y + canvas_h)
        # 四舍五入而不是截断：0.2 + 0.1×2 这类浮点误差不该把像素拉偏 1
        return round(self._window_left + cx), round(self._window_top + cy)

    def _ratio_to_screen(self, cx_ratio: float, cy_ratio: float) -> tuple[int, int]:
        """画布内归一化坐标 → 屏幕坐标"""
        w, h = self._capture_size()
        canvas = self._layout.get_canvas()
        sx = canvas.x_ratio + cx_ratio * canvas.w_ratio
        sy = canvas.y_ratio + cy_ratio * canvas.h_ratio
        return int(self._window_left + sx * w), int(self._window_top + sy * h)
