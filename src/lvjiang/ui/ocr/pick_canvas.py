"""取点/取色/取区域画布 —— 脚本工作台用的截图画布

在 OCRCanvas（缩放、平移、可拖拽选框）之上加三个信号，把"抓抓式取点器"接进来：
- hovered(x, y, r, g, b)：鼠标移到图上，归一化坐标 + 像素 RGB（状态行实时显示）
- picked(x, y, r, g, b)：左键单击（没拖出选框）→ 取点/取色
- region_changed(x, y, w, h)：拖出/调整选框后 → 取区域

坐标都是相对当前显示图像的归一化值；调用方把截图按布局画布裁过再喂进来，
这样取到的就是 DSL 直接能用的画布坐标。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent

from .canvas import OCRCanvas

#: 按下到抬起位移小于此值（widget 像素）视为"点击"而非拖框
_CLICK_SLOP = 4.0


class PickCanvas(OCRCanvas):
    hovered = pyqtSignal(float, float, int, int, int)
    picked = pyqtSignal(float, float, int, int, int)
    region_changed = pyqtSignal(float, float, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._press_pos = None
        self._placeholder = "点「刷新截图」抓一帧；左键点=取点/取色，拖=取区域，右键拖=平移，滚轮=缩放"

    # ─── 像素读取 ──────────────────────────────────────

    def rgb_at(self, nx: float, ny: float) -> tuple[int, int, int] | None:
        img = self._original_image
        if img is None or self._img_w == 0 or self._img_h == 0:
            return None
        px = min(max(int(nx * self._img_w), 0), self._img_w - 1)
        py = min(max(int(ny * self._img_h), 0), self._img_h - 1)
        b, g, r = (int(v) for v in img[py, px][:3])
        return r, g, b

    def selection_norm(self) -> tuple[float, float, float, float] | None:
        """当前选框 (x, y, w, h) 归一化；无选框 None"""
        if self._selection is None:
            return None
        s = self._selection
        return s.x(), s.y(), s.width(), s.height()

    # ─── 事件 ──────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent):  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self._pixmap is not None:
            self._press_pos = event.position()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):  # type: ignore[override]
        super().mouseMoveEvent(event)
        if self._pixmap is None:
            return
        pos = event.position()
        n = self._widget_to_norm(pos.x(), pos.y())
        rgb = self.rgb_at(n.x(), n.y())
        if rgb is not None:
            self.hovered.emit(n.x(), n.y(), *rgb)

    def mouseReleaseEvent(self, event: QMouseEvent):  # type: ignore[override]
        is_left = event.button() == Qt.MouseButton.LeftButton
        press = self._press_pos
        self._press_pos = None
        super().mouseReleaseEvent(event)
        if not is_left or self._pixmap is None or press is None:
            return
        pos = event.position()
        moved = abs(pos.x() - press.x()) + abs(pos.y() - press.y())
        if moved <= _CLICK_SLOP and self._selection is None:
            n = self._widget_to_norm(pos.x(), pos.y())
            rgb = self.rgb_at(n.x(), n.y())
            if rgb is not None:
                self.picked.emit(n.x(), n.y(), *rgb)
            return
        sel = self.selection_norm()
        if sel is not None:
            self.region_changed.emit(*sel)
