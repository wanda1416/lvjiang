"""画布坐标转换混入类"""

from PyQt6.QtCore import QPointF, QRectF

from ...core.layout_models import CanvasConfig, Point, Region


class CanvasCoordMixin:
    """坐标转换混入类 - 提供 widget/归一化/画布 之间的坐标换算"""

    # 以下属性由主类提供
    _display_rect: QRectF
    _pixmap: object  # QPixmap | None
    _base_scale: float
    _zoom: float
    _canvas_config: CanvasConfig

    def _recalc_display(self):
        """计算图片在 widget 中的显示区域（左上角对齐，基准缩放）"""
        if not self._pixmap:
            self._display_rect = QRectF()
            return
        pw, ph = self._pixmap.width(), self._pixmap.height()
        ww, wh = self.width(), self.height()
        self._base_scale = min(ww / pw, wh / ph)
        scale = self._base_scale * self._zoom
        self._display_rect = QRectF(0, 0, pw * scale, ph * scale)

    def _apply_zoom_anchor(self, anchor_widget_pos: QPointF):
        """以 anchor_widget_pos 为锚点重新计算 display_rect，保持该点归一化坐标不变"""
        if not self._pixmap:
            return
        scale = self._base_scale * self._zoom
        pw, ph = self._pixmap.width(), self._pixmap.height()
        dw = pw * scale
        dh = ph * scale
        # 锚点在归一化坐标系中的位置
        if self._display_rect.width() > 0 and self._display_rect.height() > 0:
            nx = (anchor_widget_pos.x() - self._display_rect.x()) / self._display_rect.width()
            ny = (anchor_widget_pos.y() - self._display_rect.y()) / self._display_rect.height()
        else:
            nx, ny = 0.5, 0.5
        # 新 display_rect 使锚点归一化坐标不变
        dx = anchor_widget_pos.x() - nx * dw
        dy = anchor_widget_pos.y() - ny * dh
        self._display_rect = QRectF(dx, dy, dw, dh)

    # ─── 坐标转换 ────────────────────────────────────────

    def _widget_to_norm(self, pos: QPointF) -> tuple[float, float]:
        """widget 坐标 -> 归一化坐标（相对于图片显示区域）"""
        if self._display_rect.width() == 0:
            return 0.0, 0.0
        nx = (pos.x() - self._display_rect.x()) / self._display_rect.width()
        ny = (pos.y() - self._display_rect.y()) / self._display_rect.height()
        return max(0.0, min(1.0, nx)), max(0.0, min(1.0, ny))

    def _norm_to_widget(self, nx: float, ny: float) -> QPointF:
        """归一化坐标 -> widget 坐标"""
        wx = self._display_rect.x() + nx * self._display_rect.width()
        wy = self._display_rect.y() + ny * self._display_rect.height()
        return QPointF(wx, wy)

    def _region_rect_norm(self, r: Region) -> QRectF:
        """区域的归一化矩形"""
        return QRectF(r.x_ratio, r.y_ratio, r.w_ratio, r.h_ratio)

    def _region_rect_widget(self, r: Region) -> QRectF:
        """区域的 widget 坐标矩形（叠加画布变换）"""
        sx, sy = self._canvas_to_screenshot_norm(r.x_ratio, r.y_ratio)
        sw, sh = self._canvas_to_screenshot_norm(
            r.x_ratio + r.w_ratio, r.y_ratio + r.h_ratio
        )
        tl = self._norm_to_widget(sx, sy)
        br = self._norm_to_widget(sw, sh)
        return QRectF(tl, br)

    # ─── 画布坐标转换 ───────────────────────────────

    def _canvas_to_screenshot_norm(self, cx: float, cy: float) -> tuple[float, float]:
        """画布内归一化坐标 -> 截图归一化坐标"""
        c = self._canvas_config
        sx = c.x_ratio + cx * c.w_ratio
        sy = c.y_ratio + cy * c.h_ratio
        return sx, sy

    def _screenshot_to_canvas_norm(self, sx: float, sy: float) -> tuple[float, float]:
        """截图归一化坐标 -> 画布内归一化坐标"""
        c = self._canvas_config
        if c.w_ratio == 0 or c.h_ratio == 0:
            return sx, sy
        cx = (sx - c.x_ratio) / c.w_ratio
        cy = (sy - c.y_ratio) / c.h_ratio
        return max(0.0, min(1.0, cx)), max(0.0, min(1.0, cy))

    def _widget_delta_to_canvas_norm(
        self, start: QPointF, end: QPointF,
    ) -> tuple[float, float]:
        """widget 像素位移 -> 画布局部归一化位移。

        Region/Panel 坐标相对于画布，而 ``_widget_to_norm`` 返回相对于整张
        截图的比例。非全幅裁剪画布必须再除以画布宽高比例，否则拖动会缩小，
        拉伸则会把截图绝对坐标误当局部坐标而瞬间漂移。
        """
        display_w = self._display_rect.width()
        display_h = self._display_rect.height()
        c = self._canvas_config
        dx = ((end.x() - start.x()) / display_w / c.w_ratio
              if display_w > 0 and c.w_ratio > 0 else 0.0)
        dy = ((end.y() - start.y()) / display_h / c.h_ratio
              if display_h > 0 and c.h_ratio > 0 else 0.0)
        return dx, dy

    def _canvas_rect_widget(self) -> QRectF:
        """画布框的 widget 坐标矩形"""
        c = self._canvas_config
        tl = self._norm_to_widget(c.x_ratio, c.y_ratio)
        br = self._norm_to_widget(c.x_ratio + c.w_ratio, c.y_ratio + c.h_ratio)
        return QRectF(tl, br)

    # ─── point 坐标转换 ───────────────────────────────

    def _point_center_widget(self, p: Point) -> QPointF:
        """point 中心（画布内归一化）-> widget 坐标"""
        sx, sy = self._canvas_to_screenshot_norm(p.cx_ratio, p.cy_ratio)
        return self._norm_to_widget(sx, sy)

    def _canvas_norm_center_widget(self, cx: float, cy: float) -> QPointF:
        """画布内归一化中心 -> widget 坐标"""
        sx, sy = self._canvas_to_screenshot_norm(cx, cy)
        return self._norm_to_widget(sx, sy)

    def _point_radius_pixels(self, p: Point) -> float:
        """point 半径（归一化）-> widget 像素半径

        以画布框在 widget 中的短边为基准换算，保证圆形视觉不随宽高比拉伸。
        """
        rect = self._canvas_rect_widget()
        base = min(rect.width(), rect.height())
        return p.r_ratio * base
