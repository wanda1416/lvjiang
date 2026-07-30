"""参考图画布 - 图片显示 + 单区域框选 + 网格预览

简化版 RegionCanvas，仅支持：
- 图片加载与显示（缩放/平移）
- 单个矩形区域框选（定义网格切割范围）
- 网格线叠加预览（rows × cols）
- 单元格悬停高亮
"""

from enum import Enum, auto

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QImage,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PyQt6.QtWidgets import QWidget


class DragMode(Enum):
    NONE = auto()
    DRAWING = auto()       # 正在绘制新矩形
    MOVING = auto()        # 正在移动矩形
    RESIZING = auto()      # 正在缩放矩形


class HandlePos(Enum):
    """8 个缩放手柄位置"""
    NONE = auto()
    TOP_LEFT = auto()
    TOP = auto()
    TOP_RIGHT = auto()
    RIGHT = auto()
    BOTTOM_RIGHT = auto()
    BOTTOM = auto()
    BOTTOM_LEFT = auto()
    LEFT = auto()


HANDLE_SIZE = 8  # 手柄像素大小


class ReferenceCanvas(QWidget):
    """参考图画布 - 支持图片显示、单区域框选、网格预览、单元格选择"""

    region_changed = pyqtSignal(float, float, float, float)  # x1, y1, x2, y2 normalized
    selection_changed = pyqtSignal()  # 单元格选择变化

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 300)
        self.setMouseTracking(True)
        self.setStyleSheet("background-color: #1e1e1e;")

        # 图片
        self._pixmap: QPixmap | None = None
        self._original_image: np.ndarray | None = None  # BGR numpy，供切割
        self._img_w = 0
        self._img_h = 0

        # 显示区域（图片在 widget 内的偏移和缩放）
        self._display_rect = QRectF()
        self._base_scale = 1.0
        self._zoom = 1.0

        # 网格区域（归一化坐标 0.0~1.0，相对于图片）
        self._grid_rect: QRectF | None = None  # x, y, w, h
        self._drawing_rect: QRectF | None = None  # 绘制中的临时矩形
        self._drag_mode = DragMode.NONE
        self._drag_handle = HandlePos.NONE
        self._drag_start = QPointF()
        self._drag_orig: QRectF | None = None
        self._pending_cell_toggle: tuple[int, int] | None = None  # 待处理的单元格切换

        # 右键拖拽平移
        self._panning = False
        self._pan_start = QPointF()

        # 网格设置
        self._grid_rows = 5
        self._grid_cols = 6
        self._grid_gap = 0  # 间隔像素（原图坐标）
        self._show_grid = False
        self._hover_cell: tuple[int, int] | None = None  # (row, col)
        self._selected_cells: set[tuple[int, int]] = set()  # 已选中的单元格

        # 回调
        self.on_grid_rect_changed = None  # callable() -> None

    # ─── 公开接口 ────────────────────────────────────────

    def set_image(self, image: np.ndarray):
        """设置图片（BGR numpy 数组）"""
        self._original_image = image.copy()
        h, w = image.shape[:2]
        self._img_w = w
        self._img_h = h

        # 转 RGB -> QImage -> QPixmap
        rgb = image[:, :, ::-1].copy()
        qimg = QImage(rgb.copy().data, w, h, 3 * w, QImage.Format.Format_RGB888)
        self._pixmap = QPixmap.fromImage(qimg)

        self._grid_rect = None
        self._show_grid = False
        self._fit_to_widget()
        self.update()

    def clear_image(self):
        """清空图片"""
        self._pixmap = None
        self._original_image = None
        self._img_w = 0
        self._img_h = 0
        self._grid_rect = None
        self._show_grid = False
        self._zoom = 1.0
        self.update()

    def get_image(self) -> np.ndarray | None:
        """获取原始 numpy 图片"""
        return self._original_image

    @property
    def has_image(self) -> bool:
        return self._pixmap is not None

    # ─── 网格区域 ─────────────────────────────────────────

    def set_grid_rect(self, rect: QRectF | None):
        """设置网格区域（归一化坐标）"""
        self._grid_rect = rect
        self.update()

    def get_grid_rect(self) -> QRectF | None:
        """获取网格区域（归一化坐标）"""
        return self._grid_rect

    def set_grid_size(self, rows: int, cols: int):
        """设置网格行列数"""
        self._grid_rows = max(1, rows)
        self._grid_cols = max(1, cols)
        self._selected_cells.clear()  # 重置选择
        self.update()

    def set_grid_gap(self, gap: int):
        """设置网格间隔（原图像素）"""
        self._grid_gap = gap
        self.update()

    def select_all_cells(self):
        """全选所有单元格"""
        self._selected_cells.clear()
        for r in range(self._grid_rows):
            for c in range(self._grid_cols):
                self._selected_cells.add((r, c))
        self.selection_changed.emit()
        self.update()

    def deselect_all_cells(self):
        """全不选所有单元格"""
        self._selected_cells.clear()
        self.selection_changed.emit()
        self.update()

    def toggle_cell(self, row: int, col: int):
        """切换单元格选择状态"""
        cell = (row, col)
        if cell in self._selected_cells:
            self._selected_cells.discard(cell)
        else:
            self._selected_cells.add(cell)
        self.selection_changed.emit()
        self.update()

    def get_selected_cells(self) -> set[tuple[int, int]]:
        """获取已选中的单元格集合"""
        return self._selected_cells.copy()

    def has_selection(self) -> bool:
        """是否有选中的单元格"""
        return len(self._selected_cells) > 0

    @property
    def grid_rows(self) -> int:
        return self._grid_rows

    @property
    def grid_cols(self) -> int:
        return self._grid_cols

    def set_show_grid(self, show: bool):
        """设置是否显示网格线"""
        self._show_grid = show
        self.update()

    @property
    def show_grid(self) -> bool:
        return self._show_grid

    def get_region(self) -> tuple[float, float, float, float] | None:
        """获取当前框选区域（归一化坐标 x1,y1,x2,y2）"""
        if self._grid_rect is None:
            return None
        r = self._grid_rect
        return (r.x(), r.y(), r.x() + r.width(), r.y() + r.height())

    def get_region_pixels(self) -> tuple[int, int, int, int] | None:
        """获取当前框选区域像素坐标 (x1, y1, x2, y2)"""
        if self._grid_rect is None or self._original_image is None:
            return None
        h, w = self._original_image.shape[:2]
        x1 = int(self._grid_rect.x() * w)
        y1 = int(self._grid_rect.y() * h)
        x2 = int((self._grid_rect.x() + self._grid_rect.width()) * w)
        y2 = int((self._grid_rect.y() + self._grid_rect.height()) * h)
        return (x1, y1, x2, y2)

    def set_region_from_pixels(self, x: int, y: int, w: int, h: int):
        """从像素坐标设置网格区域（用于“生成网格”功能）

        Args:
            x, y: 左上角像素坐标
            w, h: 宽高像素
        """
        if self._original_image is None:
            return
        img_h, img_w = self._original_image.shape[:2]
        # 转换为归一化坐标
        nx = x / img_w
        ny = y / img_h
        nw = w / img_w
        nh = h / img_h
        # 限制在 [0, 1] 范围内
        nx = max(0.0, min(1.0, nx))
        ny = max(0.0, min(1.0, ny))
        nw = max(0.0, min(1.0 - nx, nw))
        nh = max(0.0, min(1.0 - ny, nh))
        self._grid_rect = QRectF(nx, ny, nw, nh)
        self._show_grid = True
        self._selected_cells.clear()
        self.update()
        # 发射信号通知区域变化
        self.region_changed.emit(nx, ny, nx + nw, ny + nh)

    @property
    def image_size(self) -> tuple[int, int] | None:
        """获取图片尺寸 (width, height)，None 表示无图片"""
        if self._original_image is None:
            return None
        h, w = self._original_image.shape[:2]
        return (w, h)

    def get_grid_cells(self, gap: int = 0) -> list[tuple[int, int, int, int]] | None:
        """获取网格切割的单元格像素坐标列表

        Args:
            gap: 网格线间隔（像素），用于过滤黑边

        Returns:
            [(x1, y1, x2, y2), ...] 按行优先顺序，None 表示无有效网格区域
        """
        if self._grid_rect is None or self._original_image is None:
            return None

        h, w = self._original_image.shape[:2]
        gx = int(self._grid_rect.x() * w)
        gy = int(self._grid_rect.y() * h)
        gw = int(self._grid_rect.width() * w)
        gh = int(self._grid_rect.height() * h)

        # 计算每个 cell 的尺寸（扣除间隔）
        total_gap_w = gap * (self._grid_cols - 1)
        total_gap_h = gap * (self._grid_rows - 1)
        cell_w = (gw - total_gap_w) // self._grid_cols
        cell_h = (gh - total_gap_h) // self._grid_rows

        cells = []
        for row in range(self._grid_rows):
            for col in range(self._grid_cols):
                x1 = gx + col * (cell_w + gap)
                y1 = gy + row * (cell_h + gap)
                x2 = x1 + cell_w
                y2 = y1 + cell_h
                cells.append((x1, y1, x2, y2))

        return cells

    # ─── 坐标转换 ─────────────────────────────────────────

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

    def _widget_to_normalized(self, wx: float, wy: float) -> QPointF:
        """widget 坐标 -> 图片归一化坐标"""
        if self._display_rect.width() == 0 or self._display_rect.height() == 0:
            return QPointF(0, 0)
        nx = (wx - self._display_rect.x()) / self._display_rect.width()
        ny = (wy - self._display_rect.y()) / self._display_rect.height()
        return QPointF(max(0.0, min(1.0, nx)), max(0.0, min(1.0, ny)))

    def _normalized_to_widget(self, nx: float, ny: float) -> QPointF:
        """归一化坐标 -> widget 坐标"""
        wx = self._display_rect.x() + nx * self._display_rect.width()
        wy = self._display_rect.y() + ny * self._display_rect.height()
        return QPointF(wx, wy)

    def _rect_to_widget(self, rect: QRectF) -> QRectF:
        """归一化矩形 -> widget 坐标矩形"""
        p1 = self._normalized_to_widget(rect.x(), rect.y())
        p2 = self._normalized_to_widget(rect.x() + rect.width(), rect.y() + rect.height())
        return QRectF(p1, p2).normalized()

    # ─── 命中检测 ─────────────────────────────────────────

    def _hit_handle(self, widget_pos: QPointF) -> HandlePos:
        """检测是否命中缩放手柄"""
        if self._grid_rect is None:
            return HandlePos.NONE
        wr = self._rect_to_widget(self._grid_rect)
        hs = HANDLE_SIZE
        positions = {
            HandlePos.TOP_LEFT: wr.topLeft(),
            HandlePos.TOP: QPointF(wr.center().x(), wr.top()),
            HandlePos.TOP_RIGHT: wr.topRight(),
            HandlePos.RIGHT: QPointF(wr.right(), wr.center().y()),
            HandlePos.BOTTOM_RIGHT: wr.bottomRight(),
            HandlePos.BOTTOM: QPointF(wr.center().x(), wr.bottom()),
            HandlePos.BOTTOM_LEFT: wr.bottomLeft(),
            HandlePos.LEFT: QPointF(wr.left(), wr.center().y()),
        }
        for handle, pos in positions.items():
            if abs(widget_pos.x() - pos.x()) < hs and abs(widget_pos.y() - pos.y()) < hs:
                return handle
        return HandlePos.NONE

    def _hit_rect(self, widget_pos: QPointF) -> bool:
        """检测是否命中矩形内部"""
        if self._grid_rect is None:
            return False
        wr = self._rect_to_widget(self._grid_rect)
        return wr.contains(widget_pos)

    def _get_hover_cell(self, widget_pos: QPointF) -> tuple[int, int] | None:
        """获取鼠标悬停的网格单元格"""
        if not self._show_grid or self._grid_rect is None:
            return None
        wr = self._rect_to_widget(self._grid_rect)
        if not wr.contains(widget_pos):
            return None

        # 计算 cell 尺寸（与 _draw_grid 一致）
        h, w = self._original_image.shape[:2] if self._original_image is not None else (1, 1)
        total_gap_w = self._grid_gap * (self._grid_cols - 1)
        total_gap_h = self._grid_gap * (self._grid_rows - 1)
        cell_w = (wr.width() - total_gap_w * wr.width() / w) / self._grid_cols
        cell_h = (wr.height() - total_gap_h * wr.height() / h) / self._grid_rows
        gap_w = self._grid_gap * wr.width() / w
        gap_h = self._grid_gap * wr.height() / h

        # 计算相对于区域左上角的偏移
        dx = widget_pos.x() - wr.x()
        dy = widget_pos.y() - wr.y()

        # 计算列和行（考虑间隔）
        col = int(dx / (cell_w + gap_w))
        row = int(dy / (cell_h + gap_h))
        col = max(0, min(self._grid_cols - 1, col))
        row = max(0, min(self._grid_rows - 1, row))

        # 检查是否点击在间隔区域（如果是则返回 None）
        cell_x = col * (cell_w + gap_w)
        cell_y = row * (cell_h + gap_h)
        if dx > cell_x + cell_w or dy > cell_y + cell_h:
            return None

        return (row, col)

    # ─── 绘制 ─────────────────────────────────────────────

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 绘制图片
        if self._pixmap:
            painter.drawPixmap(
                int(self._display_rect.x()), int(self._display_rect.y()),
                int(self._display_rect.width()), int(self._display_rect.height()),
                self._pixmap,
            )

        # 绘制网格区域矩形
        if self._grid_rect:
            wr = self._rect_to_widget(self._grid_rect)

            # 半透明填充
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(100, 200, 255, 40)))
            painter.drawRect(wr)

            # 边框
            painter.setBrush(Qt.BrushStyle.NoBrush)
            pen = QPen(QColor(100, 200, 255), 2)
            painter.setPen(pen)
            painter.drawRect(wr)

            # 缩放手柄
            self._draw_handles(painter, wr)

            # 网格线
            if self._show_grid:
                self._draw_grid(painter, wr)

            # 悬停单元格高亮
            if self._hover_cell and self._show_grid:
                self._draw_hover_cell(painter, wr)

        # 绘制正在拖拽的预览矩形
        if self._drawing_rect and self._drawing_rect.width() > 0.001:
            dr = self._rect_to_widget(self._drawing_rect)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(100, 200, 255, 30)))
            painter.drawRect(dr)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            pen = QPen(QColor(100, 200, 255, 150), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawRect(dr)

        painter.end()

    def _draw_handles(self, painter: QPainter, wr: QRectF):
        """绘制 8 个缩放手柄"""
        painter.setBrush(QBrush(QColor(255, 255, 255, 200)))
        painter.setPen(QPen(QColor(100, 200, 255), 1))
        hs = HANDLE_SIZE
        positions = [
            wr.topLeft(),
            QPointF(wr.center().x(), wr.top()),
            wr.topRight(),
            QPointF(wr.right(), wr.center().y()),
            wr.bottomRight(),
            QPointF(wr.center().x(), wr.bottom()),
            wr.bottomLeft(),
            QPointF(wr.left(), wr.center().y()),
        ]
        for pos in positions:
            painter.drawRect(int(pos.x() - hs / 2), int(pos.y() - hs / 2), hs, hs)

    def _draw_grid(self, painter: QPainter, wr: QRectF):
        """绘制网格线和间隔区域"""
        # 计算 cell 尺寸
        h, w = self._original_image.shape[:2] if self._original_image is not None else (1, 1)
        total_gap_w = self._grid_gap * (self._grid_cols - 1)
        total_gap_h = self._grid_gap * (self._grid_rows - 1)
        cell_w = (wr.width() - total_gap_w * wr.width() / w) / self._grid_cols
        cell_h = (wr.height() - total_gap_h * wr.height() / h) / self._grid_rows
        gap_w = self._grid_gap * wr.width() / w
        gap_h = self._grid_gap * wr.height() / h

        if self._grid_gap > 0:
            # 绘制间隔区域（半透明红色填充，精确绘制每个间隙块）
            painter.setPen(QPen(QColor(255, 80, 80, 150), 1))
            painter.setBrush(QBrush(QColor(255, 80, 80, 80)))

            # 绘制水平间隙（每行之间的间隙块，仅覆盖 cell 宽度）
            for row in range(self._grid_rows - 1):
                gy = wr.y() + (row + 1) * cell_h + row * gap_h
                for col in range(self._grid_cols):
                    cx = wr.x() + col * (cell_w + gap_w)
                    painter.drawRect(QRectF(cx, gy, cell_w, gap_h))

            # 绘制垂直间隙（每列之间的间隙块，覆盖全高）
            for col in range(self._grid_cols - 1):
                gx = wr.x() + (col + 1) * cell_w + col * gap_w
                painter.drawRect(QRectF(gx, wr.y(), gap_w, wr.height()))
        else:
            # 无间隔时绘制黄色虚线网格
            pen = QPen(QColor(255, 200, 50, 180), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)

            # 垂直线
            for i in range(1, self._grid_cols):
                x = wr.x() + i * cell_w
                painter.drawLine(int(x), int(wr.y()), int(x), int(wr.bottom()))
            # 水平线
            for i in range(1, self._grid_rows):
                y = wr.y() + i * cell_h
                painter.drawLine(int(wr.x()), int(y), int(wr.right()), int(y))

        # 绘制单元格选择框
        self._draw_cell_checkboxes(painter, wr, cell_w, cell_h, gap_w, gap_h)

    def _draw_cell_checkboxes(self, painter: QPainter, wr: QRectF,
                               cell_w: float, cell_h: float,
                               gap_w: float, gap_h: float):
        """绘制单元格选择框"""
        checkbox_size = 16  # 像素
        for row in range(self._grid_rows):
            for col in range(self._grid_cols):
                # 计算 cell 左上角位置
                x = wr.x() + col * (cell_w + gap_w)
                y = wr.y() + row * (cell_h + gap_h)

                # 选择状态
                is_selected = (row, col) in self._selected_cells

                # 绘制半透明背景表示选中
                if is_selected:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QBrush(QColor(100, 255, 100, 40)))
                    painter.drawRect(int(x), int(y), int(cell_w), int(cell_h))

                # 绘制 checkbox
                painter.setPen(QPen(QColor(255, 255, 255, 200), 1.5))
                if is_selected:
                    painter.setBrush(QBrush(QColor(80, 200, 80, 220)))
                else:
                    painter.setBrush(QBrush(QColor(50, 50, 50, 180)))

                cb_x = x + 4
                cb_y = y + 4
                painter.drawRect(int(cb_x), int(cb_y), checkbox_size, checkbox_size)

                # 选中时绘制勾
                if is_selected:
                    painter.setPen(QPen(QColor(255, 255, 255), 2))
                    painter.drawLine(
                        int(cb_x + 3), int(cb_y + checkbox_size // 2),
                        int(cb_x + checkbox_size // 2 - 1), int(cb_y + checkbox_size - 4)
                    )
                    painter.drawLine(
                        int(cb_x + checkbox_size // 2 - 1), int(cb_y + checkbox_size - 4),
                        int(cb_x + checkbox_size - 3), int(cb_y + 3)
                    )

    def _draw_hover_cell(self, painter: QPainter, wr: QRectF):
        """绘制悬停单元格高亮"""
        if not self._hover_cell:
            return
        row, col = self._hover_cell
        h, w = self._original_image.shape[:2] if self._original_image is not None else (1, 1)
        total_gap_w = self._grid_gap * (self._grid_cols - 1)
        total_gap_h = self._grid_gap * (self._grid_rows - 1)
        cell_w = (wr.width() - total_gap_w * wr.width() / w) / self._grid_cols
        cell_h = (wr.height() - total_gap_h * wr.height() / h) / self._grid_rows
        gap_w = self._grid_gap * wr.width() / w
        gap_h = self._grid_gap * wr.height() / h

        x = wr.x() + col * (cell_w + gap_w)
        y = wr.y() + row * (cell_h + gap_h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(255, 255, 100, 60)))
        painter.drawRect(int(x), int(y), int(cell_w), int(cell_h))

    # ─── 鼠标事件 ─────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position()
            handle = self._hit_handle(pos)
            if handle != HandlePos.NONE:
                self._drag_mode = DragMode.RESIZING
                self._drag_handle = handle
            elif self._show_grid and self._hit_rect(pos):
                # 网格显示时，记录点击位置，松开时判断是单击还是拖拽
                self._pending_cell_toggle = self._get_hover_cell(pos)
                self._drag_start = pos  # 保存 widget 坐标
            elif self._hit_rect(pos):
                self._drag_mode = DragMode.MOVING
            else:
                # 开始绘制新矩形（不影响已有 _grid_rect）
                self._drag_mode = DragMode.DRAWING
                self._drag_start = self._widget_to_normalized(pos.x(), pos.y())
                self._drawing_rect = QRectF(self._drag_start.x(), self._drag_start.y(), 0, 0)
            if self._drag_mode != DragMode.DRAWING and self._pending_cell_toggle is None:
                self._drag_orig = QRectF(self._grid_rect) if self._grid_rect else None
            self.update()

        elif event.button() == Qt.MouseButton.RightButton:
            pos = event.position()
            if self._hit_rect(pos):
                # 右键在区域内：移动区域
                self._drag_mode = DragMode.MOVING
                self._drag_start = self._widget_to_normalized(pos.x(), pos.y())
                self._drag_orig = QRectF(self._grid_rect) if self._grid_rect else None
            else:
                # 右键在区域外：平移截图
                self._panning = True
                self._pan_start = pos

    def mouseMoveEvent(self, event: QMouseEvent):
        pos = event.position()

        # 如果有待处理的单元格切换，但鼠标移动了，则取消（说明是拖拽而非单击）
        if self._pending_cell_toggle is not None:
            delta = pos - self._drag_start
            if delta.manhattanLength() > 5:
                self._pending_cell_toggle = None
                self._drag_mode = DragMode.MOVING
                self._drag_orig = QRectF(self._grid_rect) if self._grid_rect else None

        # 更新悬停单元格
        new_hover = self._get_hover_cell(pos)
        if new_hover != self._hover_cell:
            self._hover_cell = new_hover
            self.update()

        if self._panning:
            delta = pos - self._pan_start
            self._display_rect.translate(delta)
            self._pan_start = pos
            self.update()
            return

        if self._drag_mode == DragMode.NONE:
            # 更新鼠标样式
            handle = self._hit_handle(pos)
            if handle != HandlePos.NONE:
                self._set_cursor_for_handle(handle)
            elif self._hit_rect(pos):
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.setCursor(Qt.CursorShape.CrossCursor)
            return

        norm = self._widget_to_normalized(pos.x(), pos.y())

        if self._drag_mode == DragMode.DRAWING:
            x1, y1 = self._drag_start.x(), self._drag_start.y()
            x2, y2 = norm.x(), norm.y()
            self._drawing_rect = QRectF(
                min(x1, x2), min(y1, y2),
                abs(x2 - x1), abs(y2 - y1),
            ).normalized()

        elif self._drag_mode == DragMode.MOVING and self._drag_orig:
            self._grid_rect = self._move_rect(self._drag_orig, norm, self._drag_start)

        elif self._drag_mode == DragMode.RESIZING and self._drag_orig:
            self._grid_rect = self._resize_rect(self._drag_orig, self._drag_handle, norm)

        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            # 处理待处理的单元格切换（单击切换，拖拽则不切换）
            if self._pending_cell_toggle is not None:
                pending = self._pending_cell_toggle
                self._pending_cell_toggle = None
                if self._drag_mode == DragMode.NONE:
                    # 真正的单击（没有拖拽）
                    self.toggle_cell(pending[0], pending[1])
                    self.update()
                    return
            if self._drag_mode == DragMode.DRAWING and self._drawing_rect:
                # 拖拽足够大则提交新矩形，否则保留原有矩形
                if self._drawing_rect.width() >= 0.01 and self._drawing_rect.height() >= 0.01:
                    self._grid_rect = self._drawing_rect
                self._drawing_rect = None
            self._drag_mode = DragMode.NONE
            self._drag_handle = HandlePos.NONE
            if self.on_grid_rect_changed:
                self.on_grid_rect_changed()
            if self._grid_rect:
                self.region_changed.emit(
                    self._grid_rect.x(), self._grid_rect.y(),
                    self._grid_rect.x() + self._grid_rect.width(),
                    self._grid_rect.y() + self._grid_rect.height(),
                )
            self.update()

        elif event.button() == Qt.MouseButton.RightButton:
            if self._drag_mode == DragMode.MOVING:
                self._drag_mode = DragMode.NONE
                self._drag_handle = HandlePos.NONE
                if self.on_grid_rect_changed:
                    self.on_grid_rect_changed()
                if self._grid_rect:
                    self.region_changed.emit(
                        self._grid_rect.x(), self._grid_rect.y(),
                        self._grid_rect.x() + self._grid_rect.width(),
                        self._grid_rect.y() + self._grid_rect.height(),
                    )
            self._panning = False

    def _move_rect(self, orig: QRectF, current_norm: QPointF, drag_start: QPointF) -> QRectF:
        """移动矩形（保持大小，限制在 [0,1] 内）"""
        dx = current_norm.x() - drag_start.x()
        dy = current_norm.y() - drag_start.y()
        new_x = max(0, min(1 - orig.width(), orig.x() + dx))
        new_y = max(0, min(1 - orig.height(), orig.y() + dy))
        return QRectF(new_x, new_y, orig.width(), orig.height())

    def _resize_rect(self, orig: QRectF, handle: HandlePos, current_norm: QPointF) -> QRectF:
        """缩放矩形"""
        x1, y1 = orig.x(), orig.y()
        x2, y2 = orig.x() + orig.width(), orig.y() + orig.height()
        nx, ny = current_norm.x(), current_norm.y()

        if handle in (HandlePos.TOP_LEFT, HandlePos.LEFT, HandlePos.BOTTOM_LEFT):
            x1 = max(0, min(x2 - 0.02, nx))
        if handle in (HandlePos.TOP_RIGHT, HandlePos.RIGHT, HandlePos.BOTTOM_RIGHT):
            x2 = max(x1 + 0.02, min(1.0, nx))
        if handle in (HandlePos.TOP_LEFT, HandlePos.TOP, HandlePos.TOP_RIGHT):
            y1 = max(0, min(y2 - 0.02, ny))
        if handle in (HandlePos.BOTTOM_LEFT, HandlePos.BOTTOM, HandlePos.BOTTOM_RIGHT):
            y2 = max(y1 + 0.02, min(1.0, ny))

        return QRectF(x1, y1, x2 - x1, y2 - y1).normalized()

    def _set_cursor_for_handle(self, handle: HandlePos):
        cursors = {
            HandlePos.TOP_LEFT: Qt.CursorShape.SizeFDiagCursor,
            HandlePos.TOP: Qt.CursorShape.SizeVerCursor,
            HandlePos.TOP_RIGHT: Qt.CursorShape.SizeBDiagCursor,
            HandlePos.RIGHT: Qt.CursorShape.SizeHorCursor,
            HandlePos.BOTTOM_RIGHT: Qt.CursorShape.SizeFDiagCursor,
            HandlePos.BOTTOM: Qt.CursorShape.SizeVerCursor,
            HandlePos.BOTTOM_LEFT: Qt.CursorShape.SizeBDiagCursor,
            HandlePos.LEFT: Qt.CursorShape.SizeHorCursor,
        }
        self.setCursor(cursors.get(handle, Qt.CursorShape.ArrowCursor))

    # ─── 滚轮缩放（以鼠标位置为中心）──────────────────────

    def wheelEvent(self, event: QWheelEvent):
        if self._pixmap is None:
            return
        pos = event.position()
        # 缩放前：鼠标对应的归一化坐标
        norm_before = self._widget_to_normalized(pos.x(), pos.y())

        delta = event.angleDelta().y()
        factor = 1.15 if delta > 0 else 1 / 1.15
        self._zoom = max(0.1, min(10.0, self._zoom * factor))
        self._update_display_rect()

        # 缩放后：同一归一化坐标对应的 widget 坐标
        new_widget = self._normalized_to_widget(norm_before.x(), norm_before.y())
        # 平移使鼠标下的图片位置不变
        self._display_rect.translate(pos.x() - new_widget.x(), pos.y() - new_widget.y())
        self.update()

    # ─── 窗口大小变化 ─────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_to_widget()
