"""OCR 测试画布 - 图片显示 + 缩放/平移 + OCR 结果标注 + 选框交互

参考 reference_manager/canvas.py 的缩放平移实现。
参考 scene_editor/canvas.py 的选框交互实现。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QColor,
    QFont,
    QImage,
    QMouseEvent,
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


class _HandlePos(Enum):
    """8 个缩放手柄位置"""
    TOP_LEFT = auto()
    TOP = auto()
    TOP_RIGHT = auto()
    RIGHT = auto()
    BOTTOM_RIGHT = auto()
    BOTTOM = auto()
    BOTTOM_LEFT = auto()
    LEFT = auto()


class _DragMode(Enum):
    NONE = auto()
    DRAWING = auto()    # 正在绘制选框
    MOVING = auto()     # 正在移动选框
    RESIZING = auto()   # 正在拉伸选框


_HANDLE_SIZE = 6  # 手柄半尺寸

# 手柄光标映射
_HANDLE_CURSORS = {
    _HandlePos.TOP_LEFT:     Qt.CursorShape.SizeFDiagCursor,
    _HandlePos.BOTTOM_RIGHT: Qt.CursorShape.SizeFDiagCursor,
    _HandlePos.TOP_RIGHT:    Qt.CursorShape.SizeBDiagCursor,
    _HandlePos.BOTTOM_LEFT:  Qt.CursorShape.SizeBDiagCursor,
    _HandlePos.TOP:          Qt.CursorShape.SizeVerCursor,
    _HandlePos.BOTTOM:       Qt.CursorShape.SizeVerCursor,
    _HandlePos.LEFT:         Qt.CursorShape.SizeHorCursor,
    _HandlePos.RIGHT:        Qt.CursorShape.SizeHorCursor,
}


class OCRCanvas(QWidget):
    """OCR 测试画布：图片显示 + 缩放/平移 + OCR 结果标注 + 红色选框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 300)
        self.setMouseTracking(True)
        self.setStyleSheet("background-color: #2b2b2b;")

        # 图片
        self._pixmap: QPixmap | None = None
        self._original_image: np.ndarray | None = None  # 原始 BGR numpy 数组，供 OCR
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

        # 选框（图像像素坐标，归一化 0~1 相对于图像尺寸）
        self._selection: QRectF | None = None  # (x, y, w, h) 归一化坐标
        self._drag_mode = _DragMode.NONE
        self._drag_handle: _HandlePos | None = None
        self._drag_start = QPointF()  # widget 坐标
        self._drag_orig: QRectF | None = None  # 归一化坐标

        # 提示文字
        self._placeholder = "Ctrl+V 粘贴截图，或点击「上传图片」加载文件"

    # ─── 公开接口 ────────────────────────────────────────

    def set_image(self, image: np.ndarray):
        """设置背景图片（BGR numpy 数组）"""
        rgb = np.ascontiguousarray(image[:, :, ::-1])
        h, w = rgb.shape[:2]
        self._img_w = w
        self._img_h = h
        qimg = QImage(bytes(rgb.data), w, h, w * 3, QImage.Format.Format_RGB888).copy()
        self._pixmap = QPixmap.fromImage(qimg)
        self._original_image = image  # 保存原始 numpy 数组，供 OCR 使用
        self._zoom = 1.0
        self._ocr_boxes.clear()
        self._selection = None
        self._fit_to_widget()
        self.update()

    def get_image(self) -> np.ndarray | None:
        """获取原始截图 numpy 数组（供 OCR 裁剪）"""
        return self._original_image

    def set_pixmap(self, pixmap: QPixmap):
        """设置图片并重置视图"""
        self._pixmap = pixmap
        self._img_w = pixmap.width()
        self._img_h = pixmap.height()
        self._zoom = 1.0
        self._ocr_boxes.clear()
        self._selection = None
        self._original_image = None  # QPixmap 路径无法提供 numpy 数组
        self._fit_to_widget()
        self.update()

    def set_ocr_boxes(self, boxes: list[OCRBox]):
        """设置 OCR 识别结果并刷新"""
        self._ocr_boxes = boxes
        self.update()

    def clear(self):
        """清空图片和 OCR 结果"""
        self._pixmap = None
        self._original_image = None
        self._ocr_boxes.clear()
        self._selection = None
        self._placeholder = "Ctrl+V 粘贴截图，或点击「上传图片」加载文件"
        self.update()

    def clear_selection(self):
        """清除选框"""
        self._selection = None
        self.update()

    def has_selection(self) -> bool:
        """是否有选框"""
        return self._selection is not None

    def get_selection_pixels(self) -> tuple[int, int, int, int] | None:
        """获取选框的像素坐标 (x1, y1, x2, y2)，无选框返回 None"""
        if self._selection is None or self._img_w == 0 or self._img_h == 0:
            return None
        x = int(self._selection.x() * self._img_w)
        y = int(self._selection.y() * self._img_h)
        w = int(self._selection.width() * self._img_w)
        h = int(self._selection.height() * self._img_h)
        return (x, y, x + w, y + h)

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

    def _widget_to_norm(self, wx: float, wy: float) -> QPointF:
        """widget 坐标 -> 归一化坐标 (0~1)"""
        if self._display_rect.width() == 0 or self._display_rect.height() == 0:
            return QPointF(0, 0)
        nx = (wx - self._display_rect.x()) / self._display_rect.width()
        ny = (wy - self._display_rect.y()) / self._display_rect.height()
        return QPointF(max(0.0, min(1.0, nx)), max(0.0, min(1.0, ny)))

    def _norm_to_widget_rect(self, norm_rect: QRectF) -> QRectF:
        """归一化矩形 -> widget 矩形"""
        p1 = self._img_to_widget(
            norm_rect.x() * self._img_w,
            norm_rect.y() * self._img_h,
        )
        p2 = self._img_to_widget(
            (norm_rect.x() + norm_rect.width()) * self._img_w,
            (norm_rect.y() + norm_rect.height()) * self._img_h,
        )
        return QRectF(p1, p2).normalized()

    # ─── 绘制 ────────────────────────────────────────────

    def paintEvent(self, event: QPaintEvent):  # type: ignore[override]
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

        # 绘制选框
        if self._selection is not None:
            self._draw_selection(painter)

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

    def _draw_selection(self, painter: QPainter):
        """绘制选框（红色边框 + 8 个手柄）"""
        assert self._selection is not None
        widget_rect = self._norm_to_widget_rect(self._selection)

        # 半透明红色填充
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 0, 0, 30))
        painter.drawRect(widget_rect)

        # 红色边框
        painter.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(QColor(255, 50, 50), 2)
        painter.setPen(pen)
        painter.drawRect(widget_rect)

        # 8 个手柄
        handles = self._get_handle_positions_widget(widget_rect)
        painter.setBrush(QColor(255, 255, 255))
        painter.setPen(QPen(QColor(255, 50, 50), 1))
        for center in handles.values():
            painter.drawRect(
                int(center.x() - _HANDLE_SIZE), int(center.y() - _HANDLE_SIZE),
                _HANDLE_SIZE * 2, _HANDLE_SIZE * 2,
            )

    def _get_handle_positions_widget(self, widget_rect: QRectF) -> dict[_HandlePos, QPointF]:
        """获取 8 个手柄的 widget 坐标"""
        cx = widget_rect.center().x()
        cy = widget_rect.center().y()
        return {
            _HandlePos.TOP_LEFT:     widget_rect.topLeft(),
            _HandlePos.TOP:          QPointF(cx, widget_rect.top()),
            _HandlePos.TOP_RIGHT:    widget_rect.topRight(),
            _HandlePos.RIGHT:        QPointF(widget_rect.right(), cy),
            _HandlePos.BOTTOM_RIGHT: widget_rect.bottomRight(),
            _HandlePos.BOTTOM:       QPointF(cx, widget_rect.bottom()),
            _HandlePos.BOTTOM_LEFT:  widget_rect.bottomLeft(),
            _HandlePos.LEFT:         QPointF(widget_rect.left(), cy),
        }

    # ─── 选框命中检测 ────────────────────────────────────

    def _hit_selection(self, pos: QPointF) -> tuple[_DragMode, _HandlePos | None]:
        """检测鼠标位置命中选框的哪个部分
        返回: (DragMode, HandlePos or None)
        """
        if self._selection is None:
            return _DragMode.NONE, None

        widget_rect = self._norm_to_widget_rect(self._selection)

        # 先检测手柄
        handles = self._get_handle_positions_widget(widget_rect)
        for hpos, center in handles.items():
            hr = QRectF(
                center.x() - _HANDLE_SIZE, center.y() - _HANDLE_SIZE,
                _HANDLE_SIZE * 2, _HANDLE_SIZE * 2,
            )
            if hr.contains(pos):
                return _DragMode.RESIZING, hpos

        # 再检测矩形内部（移动）
        if widget_rect.contains(pos):
            return _DragMode.MOVING, None

        return _DragMode.NONE, None

    # ─── 交互：缩放/平移/选框 ────────────────────────────

    def wheelEvent(self, event: QWheelEvent):  # type: ignore[override]
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

    def mousePressEvent(self, event: QMouseEvent):  # type: ignore[override]
        if self._pixmap is None:
            return

        pos = event.position()

        # 右键：平移
        if event.button() == Qt.MouseButton.RightButton:
            self._panning = True
            self._pan_start = pos
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        # 左键：选框交互
        if event.button() == Qt.MouseButton.LeftButton:
            # 先检测是否命中已有选框
            mode, handle = self._hit_selection(pos)

            if mode == _DragMode.MOVING:
                self._drag_mode = _DragMode.MOVING
                self._drag_start = pos
                assert self._selection is not None
                self._drag_orig = QRectF(self._selection)
            elif mode == _DragMode.RESIZING:
                self._drag_mode = _DragMode.RESIZING
                self._drag_handle = handle
                self._drag_start = pos
                assert self._selection is not None
                self._drag_orig = QRectF(self._selection)
            else:
                # 空白区域：开始绘制新选框
                self._drag_mode = _DragMode.DRAWING
                self._drag_start = pos
                norm = self._widget_to_norm(pos.x(), pos.y())
                self._selection = QRectF(norm.x(), norm.y(), 0, 0)
                self._drag_orig = QRectF(self._selection)

    def mouseMoveEvent(self, event: QMouseEvent):  # type: ignore[override]
        pos = event.position()

        # 平移
        if self._panning:
            delta = pos - self._pan_start
            self._display_rect.translate(delta)
            self._pan_start = pos
            self.update()
            return

        # 选框交互
        if self._drag_mode == _DragMode.DRAWING:
            norm = self._widget_to_norm(pos.x(), pos.y())
            orig_norm = self._widget_to_norm(self._drag_start.x(), self._drag_start.y())
            x1, y1 = orig_norm.x(), orig_norm.y()
            x2, y2 = norm.x(), norm.y()
            self._selection = QRectF(
                min(x1, x2), min(y1, y2),
                abs(x2 - x1), abs(y2 - y1),
            )
            self.update()

        elif self._drag_mode == _DragMode.MOVING:
            if self._drag_orig is None:
                return
            # 计算位移（归一化）
            dx = (pos.x() - self._drag_start.x()) / self._display_rect.width()
            dy = (pos.y() - self._drag_start.y()) / self._display_rect.height()
            new_rect = QRectF(self._drag_orig)
            new_rect.translate(dx, dy)
            # 限制在图像范围内
            self._selection = self._clamp_rect(new_rect)
            self.update()

        elif self._drag_mode == _DragMode.RESIZING:
            if self._drag_orig is None or self._drag_handle is None:
                return
            norm = self._widget_to_norm(pos.x(), pos.y())
            new_rect = self._resize_rect(self._drag_orig, self._drag_handle, norm)
            self._selection = self._clamp_rect(new_rect)
            self.update()

        else:
            # 更新光标
            if self._pixmap and self._selection:
                mode, handle = self._hit_selection(pos)
                if handle is not None:
                    self.setCursor(_HANDLE_CURSORS.get(handle, Qt.CursorShape.ArrowCursor))
                elif mode == _DragMode.MOVING:
                    self.setCursor(Qt.CursorShape.SizeAllCursor)
                else:
                    self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event: QMouseEvent):  # type: ignore[override]
        # 平移结束
        if event.button() == Qt.MouseButton.RightButton:
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return

        # 选框交互结束
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_mode = _DragMode.NONE
            self._drag_handle = None
            self._drag_orig = None
            # 如果选框太小，清除它
            if self._selection is not None:
                if self._selection.width() < 0.005 or self._selection.height() < 0.005:
                    self._selection = None
            self.update()

    def _clamp_rect(self, rect: QRectF) -> QRectF:
        """限制矩形在 0~1 范围内"""
        x = max(0.0, min(1.0, rect.x()))
        y = max(0.0, min(1.0, rect.y()))
        w = max(0.0, min(1.0 - x, rect.width()))
        h = max(0.0, min(1.0 - y, rect.height()))
        return QRectF(x, y, w, h)

    def _resize_rect(
        self, orig: QRectF, handle: _HandlePos, new_norm: QPointF
    ) -> QRectF:
        """根据手柄位置计算拉伸后的矩形"""
        x1, y1 = orig.left(), orig.top()
        x2, y2 = orig.right(), orig.bottom()
        nx, ny = new_norm.x(), new_norm.y()

        match handle:
            case _HandlePos.TOP_LEFT:
                x1, y1 = nx, ny
            case _HandlePos.TOP:
                y1 = ny
            case _HandlePos.TOP_RIGHT:
                x2, y1 = nx, ny
            case _HandlePos.RIGHT:
                x2 = nx
            case _HandlePos.BOTTOM_RIGHT:
                x2, y2 = nx, ny
            case _HandlePos.BOTTOM:
                y2 = ny
            case _HandlePos.BOTTOM_LEFT:
                x1, y2 = nx, ny
            case _HandlePos.LEFT:
                x1 = nx

        return QRectF(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_to_widget()
