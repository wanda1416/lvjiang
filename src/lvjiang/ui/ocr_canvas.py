"""OCR 测试画布 - 图片显示 + 缩放/平移 + OCR 结果标注

参考 reference_manager/canvas.py 的缩放平移实现。
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PyQt6.QtWidgets import QWidget


@dataclass
class OCRBox:
    """OCR 识别结果（画布标注用）"""
    text: str
    confidence: float
    bbox: list[tuple[int, int]]  # 四角像素坐标 [(x,y), ...]


class OCRCanvas(QWidget):
    """OCR 测试画布：图片显示 + 缩放/平移 + OCR 结果红色矩形标注"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 300)
        self.setStyleSheet("background-color: #2b2b2b;")

        # 图片
        self._pixmap: QPixmap | None = None
        self._img_w = 0
        self._img_h = 0

        # 显示区域（图片在 widget 内的偏移和缩放）
        self._display_rect = QRectF()
        self._base_scale = 1.0
        self._zoom = 1.0

        # 右键拖拽平移
        self._panning = False
        self._pan_start = QPointF()

        # OCR 结果
        self._ocr_boxes: list[OCRBox] = []

        # 提示文字
        self._placeholder = "Ctrl+V 粘贴截图，或点击「上传图片」加载文件"

    # ─── 公开接口 ────────────────────────────────────────

    def set_pixmap(self, pixmap: QPixmap):
        """设置图片并重置视图"""
        self._pixmap = pixmap
        self._img_w = pixmap.width()
        self._img_h = pixmap.height()
        self._zoom = 1.0
        self._ocr_boxes.clear()
        self._fit_to_widget()
        self.update()

    def set_ocr_boxes(self, boxes: list[OCRBox]):
        """设置 OCR 识别结果并刷新"""
        self._ocr_boxes = boxes
        self.update()

    def clear(self):
        """清空图片和 OCR 结果"""
        self._pixmap = None
        self._ocr_boxes.clear()
        self._placeholder = "Ctrl+V 粘贴截图，或点击「上传图片」加载文件"
        self.update()

    def set_placeholder(self, text: str):
        """设置提示文字"""
        self._placeholder = text
        self.update()

    # ─── 坐标变换 ────────────────────────────────────────

    def _fit_to_widget(self):
        """计算适应窗口的基准缩放"""
        if self._pixmap is None:
            return
        ww, wh = self.width(), self.height()
        pw, ph = self._pixmap.width(), self._pixmap.height()
        if pw == 0 or ph == 0:
            return
        self._base_scale = min(ww / pw, wh / ph)
        self._update_display_rect()

    def _update_display_rect(self):
        """更新图片显示区域"""
        if self._pixmap is None:
            return
        scale = self._base_scale * self._zoom
        pw, ph = self._pixmap.width(), self._pixmap.height()
        dw, dh = pw * scale, ph * scale
        ww, wh = self.width(), self.height()
        self._display_rect = QRectF((ww - dw) / 2, (wh - dh) / 2, dw, dh)

    def _img_to_widget(self, ix: float, iy: float) -> QPointF:
        """图像像素坐标 -> widget 坐标"""
        if self._img_w == 0 or self._img_h == 0:
            return QPointF(0, 0)
        nx = ix / self._img_w
        ny = iy / self._img_h
        wx = self._display_rect.x() + nx * self._display_rect.width()
        wy = self._display_rect.y() + ny * self._display_rect.height()
        return QPointF(wx, wy)

    # ─── 绘制 ────────────────────────────────────────────

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._pixmap is None:
            # 无图片时显示提示文字
            painter.setPen(QColor("#888888"))
            painter.setFont(QFont("", 14))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             self._placeholder)
            painter.end()
            return

        # 绘制图片
        painter.drawPixmap(
            int(self._display_rect.x()), int(self._display_rect.y()),
            int(self._display_rect.width()), int(self._display_rect.height()),
            self._pixmap,
        )

        # 绘制 OCR 结果红色矩形
        for box in self._ocr_boxes:
            self._draw_ocr_box(painter, box)

        painter.end()

    def _draw_ocr_box(self, painter: QPainter, box: OCRBox):
        """绘制单个 OCR 结果的红色矩形框 + 文字标签"""
        if len(box.bbox) < 2:
            return

        # 将像素坐标转为 widget 坐标
        widget_pts = [self._img_to_widget(x, y) for x, y in box.bbox]

        # 计算包围矩形
        xs = [p.x() for p in widget_pts]
        ys = [p.y() for p in widget_pts]
        rect = QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

        # 半透明红色填充
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 0, 0, 40))
        painter.drawRect(rect)

        # 红色边框
        painter.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(QColor(255, 50, 50), 2)
        painter.setPen(pen)
        painter.drawRect(rect)

        # 文字标签（左上角）
        label = f"{box.text} ({box.confidence:.0%})"
        painter.setFont(QFont("Microsoft YaHei", 9))
        painter.setPen(QColor(255, 255, 255))
        # 文字背景
        fm = painter.fontMetrics()
        text_rect = fm.boundingRect(label)
        bg_rect = QRectF(rect.x(), rect.y() - text_rect.height() - 4,
                         text_rect.width() + 8, text_rect.height() + 4)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 180))
        painter.drawRect(bg_rect)
        # 文字
        painter.setPen(QColor(255, 80, 80))
        painter.drawText(int(bg_rect.x()) + 4, int(bg_rect.y()) + text_rect.height(),
                         label)

    # ─── 交互：缩放/平移 ─────────────────────────────────

    def wheelEvent(self, event: QWheelEvent):
        if self._pixmap is None:
            return
        pos = event.position()

        # 缩放前：鼠标对应的归一化坐标
        if self._display_rect.width() == 0:
            return
        norm_x = (pos.x() - self._display_rect.x()) / self._display_rect.width()
        norm_y = (pos.y() - self._display_rect.y()) / self._display_rect.height()

        delta = event.angleDelta().y()
        factor = 1.15 if delta > 0 else 1 / 1.15
        self._zoom = max(0.1, min(10.0, self._zoom * factor))
        self._update_display_rect()

        # 缩放后：保持鼠标下的图片位置不变
        new_wx = self._display_rect.x() + norm_x * self._display_rect.width()
        new_wy = self._display_rect.y() + norm_y * self._display_rect.height()
        self._display_rect.translate(pos.x() - new_wx, pos.y() - new_wy)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton and self._pixmap:
            self._panning = True
            self._pan_start = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._panning:
            pos = event.position()
            delta = pos - self._pan_start
            self._display_rect.translate(delta)
            self._pan_start = pos
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_to_widget()
