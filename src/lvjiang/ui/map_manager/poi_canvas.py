"""地图底图画布：显示底图、点选 POI、缩放平移。

坐标一律用底图归一化坐标（0–1），与 :class:`~lvjiang.core.maps.MapPoi`
一致；像素只在绘制时出现。
"""

from __future__ import annotations

import numpy as np
from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)

from ...core.maps import MapPoi
from ..theme import get_theme_manager

#: 点击落在已有 POI 这个归一化半径内视为选中它，而不是新增
POI_HIT_RATIO = 0.012
_POI_DRAW_RADIUS_PX = 7.0


def bgr_to_qimage(img: np.ndarray) -> QImage:
    """BGR ndarray → QImage（拷贝一份，脱离 numpy 缓冲区生命周期）。"""
    if img.ndim == 2:
        rgb = np.stack([img] * 3, axis=-1)
    else:
        rgb = np.ascontiguousarray(img[:, :, ::-1][:, :, :3])
    h, w = rgb.shape[:2]
    return QImage(bytes(rgb.data), w, h, 3 * w, QImage.Format.Format_RGB888).copy()


class PoiCanvas(QGraphicsView):
    """底图 + POI 标记。

    - 左键点击：命中已有 POI 发 ``poi_clicked(key)``，否则发
      ``canvas_clicked(x_ratio, y_ratio)``；
    - 滚轮缩放，中键拖动平移。
    """

    canvas_clicked = pyqtSignal(float, float)
    poi_clicked = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._image_size: tuple[int, int] = (0, 0)   # (w, h)
        self._pois: list[MapPoi] = []
        self._selected_key: str = ""
        self._marker_items: list = []
        self._editable = True
        self._pan_start: QPoint | None = None
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setMinimumSize(320, 240)

    # ─── 数据 ──────────────────────────────────────────────

    @property
    def has_image(self) -> bool:
        return self._pixmap_item is not None

    def set_editable(self, editable: bool) -> None:
        self._editable = editable

    def set_image(self, img: np.ndarray | None) -> None:
        self._scene.clear()
        self._marker_items = []
        self._pixmap_item = None
        self._image_size = (0, 0)
        if img is None:
            self._scene.setSceneRect(QRectF(0, 0, 1, 1))
            return
        pixmap = QPixmap.fromImage(bgr_to_qimage(img))
        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._image_size = (pixmap.width(), pixmap.height())
        self._scene.setSceneRect(QRectF(0, 0, pixmap.width(), pixmap.height()))
        self.fit()
        self._redraw_markers()

    def set_pois(self, pois: list[MapPoi], selected_key: str = "") -> None:
        self._pois = list(pois)
        self._selected_key = selected_key
        self._redraw_markers()

    def fit(self) -> None:
        if self._pixmap_item is not None:
            self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    # ─── 坐标 ──────────────────────────────────────────────

    def _to_ratio(self, scene_pos: QPointF) -> tuple[float, float] | None:
        w, h = self._image_size
        if w <= 0 or h <= 0:
            return None
        rx, ry = scene_pos.x() / w, scene_pos.y() / h
        if not (0.0 <= rx <= 1.0 and 0.0 <= ry <= 1.0):
            return None
        return rx, ry

    def _hit_poi(self, rx: float, ry: float) -> MapPoi | None:
        best: MapPoi | None = None
        w, h = self._image_size
        best_d = min(w, h) * POI_HIT_RATIO
        for poi in self._pois:
            d = (((poi.x - rx) * w) ** 2 + ((poi.y - ry) * h) ** 2) ** 0.5
            if d <= best_d:
                best, best_d = poi, d
        return best

    # ─── 绘制 ──────────────────────────────────────────────

    def _redraw_markers(self) -> None:
        for item in self._marker_items:
            self._scene.removeItem(item)
        self._marker_items = []
        if self._pixmap_item is None:
            return
        w, h = self._image_size
        tokens = get_theme_manager().tokens
        normal = QColor(tokens.accent)
        selected = QColor(tokens.warning)
        font = QFont()
        font.setPointSize(9)
        for poi in self._pois:
            color = selected if poi.key == self._selected_key else normal
            r = _POI_DRAW_RADIUS_PX
            cx, cy = poi.x * w, poi.y * h
            # ItemIgnoresTransformations 以 item 原点为锚：矩形围绕原点，
            # 圆心放到 pos 上，这样缩放时标记大小不变、位置跟随底图。
            dot = QGraphicsEllipseItem(-r, -r, 2 * r, 2 * r)
            dot.setPen(QPen(QColor("white"), 1.5))
            dot.setBrush(QBrush(color))
            dot.setFlag(QGraphicsEllipseItem.GraphicsItemFlag.ItemIgnoresTransformations)
            dot.setPos(cx, cy)
            self._scene.addItem(dot)
            label = QGraphicsSimpleTextItem(poi.name or poi.key)
            label.setFont(font)
            label.setBrush(QBrush(color))
            label.setFlag(QGraphicsSimpleTextItem.GraphicsItemFlag.ItemIgnoresTransformations)
            label.setPos(cx + r + 2, cy - r)
            self._scene.addItem(label)
            self._marker_items.extend((dot, label))

    # ─── 交互 ──────────────────────────────────────────────

    def mousePressEvent(self, event):  # noqa: N802 — Qt 虚函数
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_start = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._pixmap_item is not None:
            ratio = self._to_ratio(self.mapToScene(event.position().toPoint()))
            if ratio is not None:
                hit = self._hit_poi(*ratio)
                if hit is not None:
                    self.poi_clicked.emit(hit.key)
                    event.accept()
                    return
                if self._editable:
                    self.canvas_clicked.emit(*ratio)
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802 — Qt 虚函数
        if self._pan_start is not None:
            current = event.position().toPoint()
            delta = current - self._pan_start
            self._pan_start = current
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802 — Qt 虚函数
        if event.button() == Qt.MouseButton.MiddleButton and self._pan_start is not None:
            self._pan_start = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):  # noqa: N802 — Qt 虚函数
        if self._pixmap_item is None:
            return
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)
        event.accept()
