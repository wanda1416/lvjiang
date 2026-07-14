"""区域编辑器对话框 - 在截图上框选区域并绑定字段"""

from enum import Enum, auto
import numpy as np
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QWidget, QListWidget, QListWidgetItem,
    QInputDialog, QSplitter, QStatusBar,
    QTextEdit, QApplication, QTabWidget, QMessageBox,
)
from PyQt6.QtCore import Qt, QRectF, QPointF, QSize
from PyQt6.QtGui import (
    QImage, QPixmap, QPainter, QPen, QBrush, QColor,
    QMouseEvent, QPaintEvent, QCursor, QFont, QWheelEvent,
)
from loguru import logger

from ..core.region_config import (
    FIELD_GROUPS, EQUIP_FIELDS, Region, Layout, LayoutConfigManager,
    get_scene_name, get_scene_fields,
)


# ─── 颜色方案 ────────────────────────────────────────────

REGION_COLORS = [
    QColor(255, 87, 87, 60),    # 红
    QColor(87, 157, 255, 60),   # 蓝
    QColor(87, 255, 157, 60),   # 绿
    QColor(255, 197, 87, 60),   # 橙
    QColor(197, 87, 255, 60),   # 紫
    QColor(87, 255, 255, 60),   # 青
    QColor(255, 87, 197, 60),   # 粉
    QColor(157, 255, 87, 60),   # 黄绿
]

HANDLE_SIZE = 6  # 缩放手柄半尺寸


class DragMode(Enum):
    NONE = auto()
    DRAWING = auto()       # 正在绘制新矩形
    MOVING = auto()        # 正在移动矩形
    RESIZING = auto()      # 正在缩放矩形


class HandlePos(Enum):
    """8 个缩放手柄位置"""
    TOP_LEFT = auto()
    TOP = auto()
    TOP_RIGHT = auto()
    RIGHT = auto()
    BOTTOM_RIGHT = auto()
    BOTTOM = auto()
    BOTTOM_LEFT = auto()
    LEFT = auto()


# ─── 画布组件 ────────────────────────────────────────────

class RegionCanvas(QWidget):
    """可交互的图片画布，支持框选/拖拽/缩放矩形"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 300)
        self.setMouseTracking(True)
        self.setStyleSheet("background-color: #1e1e1e;")

        # 图片
        self._pixmap: QPixmap | None = None
        self._img_w = 0  # 原始图片尺寸
        self._img_h = 0

        # 显示区域（图片在 widget 内的偏移和缩放）
        self._display_rect = QRectF()  # 图片显示区域（widget 坐标）
        self._base_scale = 1.0  # 适应窗口的基准缩放
        self._zoom = 1.0        # 用户缩放倍数

        # 区域列表（归一化坐标 0.0~1.0）
        self._regions: list[Region] = []
        self._selected_idx: int = -1  # 当前选中区域索引

        # 交互状态
        self._drag_mode = DragMode.NONE
        self._drag_handle: HandlePos | None = None
        self._drag_start = QPointF()   # 鼠标按下位置（归一化）
        self._drag_orig: Region | None = None  # 拖拽前的原始区域

        # 右键拖拽平移
        self._panning = False
        self._pan_start = QPointF()

        # 信号回调
        self.on_region_changed = None  # callable() -> None

        # 当前场景的字段列表（由外部设置）
        self._current_fields = EQUIP_FIELDS

    def set_current_fields(self, fields: list[tuple[str, str]]):
        """设置当前场景的字段列表（由对话框调用）"""
        self._current_fields = fields

    # ─── 图片管理 ────────────────────────────────────────

    def set_image(self, image: np.ndarray):
        """设置背景图片（BGR numpy 数组）"""
        rgb = np.ascontiguousarray(image[:, :, ::-1])
        h, w = rgb.shape[:2]
        self._img_w = w
        self._img_h = h
        qimg = QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()
        self._pixmap = QPixmap.fromImage(qimg)
        self._zoom = 1.0  # 新图片时重置缩放
        self._recalc_display()
        self.update()

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
        """区域的 widget 坐标矩形"""
        tl = self._norm_to_widget(r.x_ratio, r.y_ratio)
        br = self._norm_to_widget(r.x_ratio + r.w_ratio, r.y_ratio + r.h_ratio)
        return QRectF(tl, br)

    # ─── 滚轮缩放 ────────────────────────────────────────

    def wheelEvent(self, event: QWheelEvent):
        """鼠标滚轮缩放，以鼠标位置为锚点"""
        if not self._pixmap:
            return
        delta = event.angleDelta().y()
        factor = 1.15 if delta > 0 else 1 / 1.15
        new_zoom = max(0.2, min(20.0, self._zoom * factor))
        if new_zoom == self._zoom:
            return
        self._zoom = new_zoom
        self._apply_zoom_anchor(event.position())
        self.update()

    # ─── 命中检测 ────────────────────────────────────────

    def _hit_test(self, pos: QPointF) -> tuple[int, HandlePos | None]:
        """
        测试鼠标位置命中了哪个元素
        返回: (region_index, handle_pos or None)
        region_index=-1 表示未命中任何区域
        handle_pos 非 None 表示命中了缩放手柄
        """
        # 先检测手柄（优先级更高）
        if self._selected_idx >= 0:
            r = self._regions[self._selected_idx]
            handle = self._hit_handle(r, pos)
            if handle is not None:
                return self._selected_idx, handle

        # 再检测矩形（从后往前，后绘制的在上层）
        for i in range(len(self._regions) - 1, -1, -1):
            rect = self._region_rect_widget(self._regions[i])
            if rect.contains(pos):
                return i, None

        return -1, None

    def _hit_handle(self, r: Region, pos: QPointF) -> HandlePos | None:
        """检测是否命中区域的缩放手柄"""
        handles = self._get_handle_positions(r)
        for hpos, center in handles.items():
            hr = QRectF(
                center.x() - HANDLE_SIZE, center.y() - HANDLE_SIZE,
                HANDLE_SIZE * 2, HANDLE_SIZE * 2,
            )
            if hr.contains(pos):
                return hpos
        return None

    def _get_handle_positions(self, r: Region) -> dict[HandlePos, QPointF]:
        """获取 8 个缩放手柄的 widget 坐标"""
        rect = self._region_rect_widget(r)
        cx = rect.center().x()
        cy = rect.center().y()
        return {
            HandlePos.TOP_LEFT:     rect.topLeft(),
            HandlePos.TOP:          QPointF(cx, rect.top()),
            HandlePos.TOP_RIGHT:    rect.topRight(),
            HandlePos.RIGHT:        QPointF(rect.right(), cy),
            HandlePos.BOTTOM_RIGHT: rect.bottomRight(),
            HandlePos.BOTTOM:       QPointF(cx, rect.bottom()),
            HandlePos.BOTTOM_LEFT:  rect.bottomLeft(),
            HandlePos.LEFT:         QPointF(rect.left(), cy),
        }

    # ─── 鼠标事件 ────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.RightButton:
            # 右键开始拖拽平移
            self._panning = True
            self._pan_start = event.position()
            self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            return
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        pos = event.position()

        # 左键：只能创建新区域，或操作从右侧面板选中的区域
        if self._selected_idx >= 0:
            # 有选中区域时，检查是否点击了该区域的手柄或内部
            r = self._regions[self._selected_idx]
            handle = self._hit_handle(r, pos)
            if handle is not None:
                # 开始缩放选中区域
                self._drag_mode = DragMode.RESIZING
                self._drag_handle = handle
                self._drag_start = pos
                self._drag_orig = Region(**r.to_dict())
                self._notify_changed()
                self.update()
                return
            rect = self._region_rect_widget(r)
            if rect.contains(pos):
                # 开始移动选中区域
                self._drag_mode = DragMode.MOVING
                self._drag_start = pos
                self._drag_orig = Region(**r.to_dict())
                self._notify_changed()
                self.update()
                return

        # 空白处：创建新区域
        nx, ny = self._widget_to_norm(pos)
        self._drag_mode = DragMode.DRAWING
        self._drag_start = QPointF(nx, ny)
        self._selected_idx = len(self._regions)
        self._regions.append(Region(
            key="", name="新区域",
            x_ratio=nx, y_ratio=ny, w_ratio=0, h_ratio=0,
        ))
        self._notify_changed()
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        pos = event.position()

        if self._panning:
            # 右键拖拽平移画布
            delta = pos - self._pan_start
            self._display_rect.translate(delta)
            self._pan_start = pos
            self.update()
            return

        if self._drag_mode == DragMode.DRAWING:
            nx, ny = self._widget_to_norm(pos)
            r = self._regions[-1]
            x0 = self._drag_start.x()
            y0 = self._drag_start.y()
            r.x_ratio = min(x0, nx)
            r.y_ratio = min(y0, ny)
            r.w_ratio = abs(nx - x0)
            r.h_ratio = abs(ny - y0)
            self.update()

        elif self._drag_mode == DragMode.MOVING:
            dx_n, dy_n = self._widget_to_norm(pos)
            dx_n -= self._widget_to_norm(self._drag_start)[0]
            dy_n -= self._widget_to_norm(self._drag_start)[1]
            r = self._regions[self._selected_idx]
            r.x_ratio = max(0, min(1 - r.w_ratio, self._drag_orig.x_ratio + dx_n))
            r.y_ratio = max(0, min(1 - r.h_ratio, self._drag_orig.y_ratio + dy_n))
            self.update()

        elif self._drag_mode == DragMode.RESIZING:
            self._apply_resize(pos)
            self.update()

        else:
            # 更新鼠标光标
            self._update_cursor(pos)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.RightButton:
            if self._panning:
                self._panning = False
                self._update_cursor(event.position())
            return
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return

        if self._drag_mode == DragMode.DRAWING:
            r = self._regions[-1]
            # 矩形太小则忽略
            if r.w_ratio < 0.01 or r.h_ratio < 0.01:
                self._regions.pop()
                self._selected_idx = -1
            else:
                # 弹出字段选择
                self._prompt_field_selection(len(self._regions) - 1)
        elif self._drag_mode in (DragMode.MOVING, DragMode.RESIZING):
            self._notify_changed()

        self._drag_mode = DragMode.NONE
        self._drag_handle = None
        self._drag_orig = None
        self.update()

    def _apply_resize(self, pos: QPointF):
        """根据手柄位置调整矩形大小"""
        nx, ny = self._widget_to_norm(pos)
        r = self._regions[self._selected_idx]
        o = self._drag_orig
        h = self._drag_handle

        x1, y1 = o.x_ratio, o.y_ratio
        x2, y2 = o.x_ratio + o.w_ratio, o.y_ratio + o.h_ratio

        if h in (HandlePos.LEFT, HandlePos.TOP_LEFT, HandlePos.BOTTOM_LEFT):
            x1 = min(nx, x2 - 0.01)
        if h in (HandlePos.RIGHT, HandlePos.TOP_RIGHT, HandlePos.BOTTOM_RIGHT):
            x2 = max(nx, x1 + 0.01)
        if h in (HandlePos.TOP, HandlePos.TOP_LEFT, HandlePos.TOP_RIGHT):
            y1 = min(ny, y2 - 0.01)
        if h in (HandlePos.BOTTOM, HandlePos.BOTTOM_LEFT, HandlePos.BOTTOM_RIGHT):
            y2 = max(ny, y1 + 0.01)

        r.x_ratio = max(0.0, x1)
        r.y_ratio = max(0.0, y1)
        r.w_ratio = min(1.0, x2) - r.x_ratio
        r.h_ratio = min(1.0, y2) - r.y_ratio

    def _update_cursor(self, pos: QPointF):
        """根据鼠标位置更新光标（仅对选中区域响应）"""
        if self._selected_idx >= 0:
            r = self._regions[self._selected_idx]
            handle = self._hit_handle(r, pos)
            if handle is not None:
                cursors = {
                    HandlePos.TOP_LEFT:     Qt.CursorShape.SizeFDiagCursor,
                    HandlePos.BOTTOM_RIGHT: Qt.CursorShape.SizeFDiagCursor,
                    HandlePos.TOP_RIGHT:    Qt.CursorShape.SizeBDiagCursor,
                    HandlePos.BOTTOM_LEFT:  Qt.CursorShape.SizeBDiagCursor,
                    HandlePos.TOP:          Qt.CursorShape.SizeVerCursor,
                    HandlePos.BOTTOM:       Qt.CursorShape.SizeVerCursor,
                    HandlePos.LEFT:         Qt.CursorShape.SizeHorCursor,
                    HandlePos.RIGHT:        Qt.CursorShape.SizeHorCursor,
                }
                self.setCursor(QCursor(cursors[handle]))
                return
            rect = self._region_rect_widget(r)
            if rect.contains(pos):
                self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
                return
        # 默认十字光标（创建模式）
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    def _prompt_field_selection(self, region_idx: int):
        """弹出字段选择对话框"""
        # 获取未绑定的字段（使用当前场景的字段列表）
        assigned = {r.key for i, r in enumerate(self._regions) if i != region_idx and r.key}
        available = [(k, n) for k, n in self._current_fields if k not in assigned]
        logger.debug(f"字段选择: current_fields={self._current_fields}, available={available}")

        if not available:
            logger.warning("所有字段已分配，请先删除已有区域")
            self._regions.pop(region_idx)
            self._selected_idx = -1
            self.update()
            return

        items = [f"{name} ({key})" for key, name in available]
        dlg = QInputDialog(self.window())
        dlg.setWindowTitle("选择字段")
        dlg.setLabelText("为该区域绑定哪个字段？")
        dlg.setComboBoxItems(items)
        dlg.setStyleSheet(
            "QInputDialog { background-color: #f0f0f0; }"
            "QLabel { color: #333; }"
            "QComboBox { background-color: white; color: #333; padding: 4px; }"
            "QPushButton { padding: 4px 16px; }"
        )
        ok = dlg.exec()
        text = dlg.textValue() if ok else ""
        if ok and text:
            idx = items.index(text)
            key, name = available[idx]
            self._regions[region_idx].key = key
            self._regions[region_idx].name = name
            self._selected_idx = -1  # 创建完成后取消选中，恢复全部显示
        else:
            # 取消则删除该区域
            self._regions.pop(region_idx)
            self._selected_idx = -1

        self._notify_changed()
        self.update()

    # ─── 公开接口 ────────────────────────────────────────

    def set_regions(self, regions: list[Region]):
        """设置区域列表（从预设加载）"""
        self._regions = [Region(**r.to_dict()) for r in regions]
        self._selected_idx = -1
        self.update()

    def get_regions(self) -> list[Region]:
        """获取当前区域列表"""
        return [Region(**r.to_dict()) for r in self._regions if r.key]

    def select_region(self, idx: int):
        """从外部选中某区域"""
        if 0 <= idx < len(self._regions):
            self._selected_idx = idx
            self.update()

    def delete_selected(self):
        """删除选中区域"""
        if self._selected_idx >= 0:
            self._regions.pop(self._selected_idx)
            self._selected_idx = -1
            self._notify_changed()
            self.update()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
        else:
            super().keyPressEvent(event)

    # ─── 绘制 ────────────────────────────────────────────

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 绘制图片
        if self._pixmap:
            painter.drawPixmap(
                int(self._display_rect.x()),
                int(self._display_rect.y()),
                int(self._display_rect.width()),
                int(self._display_rect.height()),
                self._pixmap,
            )

        # 绘制区域：选中时只显示选中区域，否则显示全部
        if self._selected_idx >= 0 and self._selected_idx < len(self._regions):
            r = self._regions[self._selected_idx]
            color = REGION_COLORS[self._selected_idx % len(REGION_COLORS)]
            self._draw_region(painter, r, color, True)
        else:
            for i, r in enumerate(self._regions):
                color = REGION_COLORS[i % len(REGION_COLORS)]
                self._draw_region(painter, r, color, False)

        painter.end()

    def _draw_region(self, painter: QPainter, r: Region, color: QColor, selected: bool):
        """绘制单个区域"""
        rect = self._region_rect_widget(r)
        if rect.width() < 1 or rect.height() < 1:
            return

        # 半透明填充
        painter.fillRect(rect, color)

        # 边框
        pen_color = color.lighter(150) if not selected else QColor(255, 255, 0)
        pen = QPen(pen_color, 2 if selected else 1)
        painter.setPen(pen)
        painter.drawRect(rect)

        # 标签
        if r.name:
            label = r.name
            font = QFont("Microsoft YaHei", 9)
            painter.setFont(font)
            fm = painter.fontMetrics()
            tw = fm.horizontalAdvance(label) + 8
            th = fm.height() + 4
            label_rect = QRectF(rect.x(), rect.y() - th, tw, th)
            # 标签背景
            painter.fillRect(label_rect, QColor(0, 0, 0, 180))
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, label)

        # 缩放手柄
        if selected:
            handles = self._get_handle_positions(r)
            painter.setPen(QPen(QColor(255, 255, 0), 1))
            painter.setBrush(QBrush(QColor(255, 255, 0)))
            for center in handles.values():
                painter.drawRect(
                    QRectF(
                        center.x() - HANDLE_SIZE, center.y() - HANDLE_SIZE,
                        HANDLE_SIZE * 2, HANDLE_SIZE * 2,
                    )
                )

    # ─── 尺寸变化 ────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 窗口大小变化时，重新计算基准缩放并应用当前 zoom
        if self._pixmap:
            pw, ph = self._pixmap.width(), self._pixmap.height()
            ww, wh = self.width(), self.height()
            self._base_scale = min(ww / pw, wh / ph)
            self._apply_zoom_anchor(QPointF(ww / 2, wh / 2))

    # ─── 通知 ────────────────────────────────────────────

    def _notify_changed(self):
        if self.on_region_changed:
            self.on_region_changed()


# ─── 场景 Tab ────────────────────────────────────────────

class SceneTab(QWidget):
    """单个场景的编辑 Tab：左侧画布 + 右侧字段列表"""

    def __init__(self, scene_key: str, image: np.ndarray, parent=None):
        super().__init__(parent)
        self._scene_key = scene_key

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧画布
        self._canvas = RegionCanvas()
        self._canvas.set_image(image)
        self._canvas.set_current_fields(get_scene_fields(scene_key))
        splitter.addWidget(self._canvas)

        # 右侧字段列表
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.addWidget(QLabel("字段列表："))

        self._field_list = QListWidget()
        self._field_list.currentRowChanged.connect(self._on_list_selection)
        right_layout.addWidget(self._field_list)
        right_layout.addStretch()

        splitter.addWidget(right_panel)
        splitter.setSizes([650, 250])

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(splitter)

        self._refresh_field_list()
        self._canvas.on_region_changed = self._refresh_field_list

    @property
    def canvas(self) -> RegionCanvas:
        return self._canvas

    @property
    def scene_key(self) -> str:
        return self._scene_key

    def get_regions(self) -> list[Region]:
        return self._canvas.get_regions()

    def set_regions(self, regions: list[Region]):
        self._canvas.set_regions(regions)
        self._refresh_field_list()

    def _refresh_field_list(self):
        """刷新字段列表，显示已绑定/未绑定状态"""
        self._field_list.blockSignals(True)
        self._field_list.clear()
        fields = get_scene_fields(self._scene_key)
        assigned = self._canvas.get_regions()
        assigned_keys = {r.key for r in assigned}

        for key, name in fields:
            if key in assigned_keys:
                region = next(r for r in assigned if r.key == key)
                item = QListWidgetItem(f"\u2713 {name}")
                item.setToolTip(
                    f"区域: ({region.x_ratio:.1%}, {region.y_ratio:.1%}) "
                    f"大小: ({region.w_ratio:.1%} x {region.h_ratio:.1%})"
                )
            else:
                item = QListWidgetItem(f"\u25cb {name}")
                item.setForeground(Qt.GlobalColor.gray)
            self._field_list.addItem(item)
        self._field_list.blockSignals(False)

    def _on_list_selection(self, row: int):
        """列表选中项变化时同步到画布"""
        if row < 0:
            return
        fields = get_scene_fields(self._scene_key)
        if row >= len(fields):
            return
        key = fields[row][0]
        regions = self._canvas.get_regions()
        for i, r in enumerate(regions):
            if r.key == key:
                self._canvas.select_region(i)
                return


# ─── 编辑器对话框 ────────────────────────────────────────

class RegionEditorDialog(QDialog):
    """区域编辑器对话框 - 布局→场景 层级结构"""

    def __init__(
        self,
        image: np.ndarray,
        refresh_callback=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("区域编辑器")
        self.setMinimumSize(900, 700)

        self._image = image
        self._manager = LayoutConfigManager()
        self._refresh_callback = refresh_callback
        self._tabs: dict[str, SceneTab] = {}  # scene_key -> SceneTab
        self._current_layout: Layout | None = None

        self._setup_ui()
        self._auto_load_active()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # ─── 顶部布局栏 ───
        top_bar = QHBoxLayout()

        # 左侧：当前布局标签
        top_bar.addWidget(QLabel("当前布局"))
        self._layout_name_label = QLabel("无布局")
        self._layout_name_label.setStyleSheet("color: red; font-weight: bold;")
        top_bar.addWidget(self._layout_name_label)

        top_bar.addSpacing(20)

        # 右侧：功能按钮
        self._btn_activate = QPushButton("激活")
        self._btn_activate.clicked.connect(self._on_activate_layout)
        top_bar.addWidget(self._btn_activate)

        self._btn_new = QPushButton("新建")
        self._btn_new.clicked.connect(self._on_new_layout)
        top_bar.addWidget(self._btn_new)

        self._btn_load = QPushButton("加载")
        self._btn_load.clicked.connect(self._on_load_layout)
        top_bar.addWidget(self._btn_load)

        self._btn_save = QPushButton("保存")
        self._btn_save.clicked.connect(self._on_save_layout)
        top_bar.addWidget(self._btn_save)

        self._btn_save_as = QPushButton("另存为")
        self._btn_save_as.clicked.connect(self._on_save_as_layout)
        top_bar.addWidget(self._btn_save_as)

        self._btn_delete = QPushButton("删除")
        self._btn_delete.clicked.connect(self._on_delete_layout)
        top_bar.addWidget(self._btn_delete)

        self._btn_refresh = QPushButton("刷新截图")
        self._btn_refresh.clicked.connect(self._on_refresh_image)
        top_bar.addWidget(self._btn_refresh)

        top_bar.addStretch()
        layout.addLayout(top_bar)

        # ─── 场景 Tab ───
        self._tab_widget = QTabWidget()
        for scene_key, (scene_name, _) in FIELD_GROUPS.items():
            tab = SceneTab(scene_key, self._image)
            self._tabs[scene_key] = tab
            self._tab_widget.addTab(tab, scene_name)
        layout.addWidget(self._tab_widget, stretch=1)

        # ─── 底部：识别 + OCR 结果 + 状态栏 ───
        bottom_row = QHBoxLayout()
        self._btn_recognize = QPushButton("识别全部字段")
        self._btn_recognize.clicked.connect(self._on_recognize)
        bottom_row.addWidget(self._btn_recognize)
        bottom_row.addStretch()
        layout.addLayout(bottom_row)

        self._result_text = QTextEdit()
        self._result_text.setReadOnly(True)
        self._result_text.setMinimumHeight(180)
        self._result_text.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 13px;"
        )
        self._result_text.setPlaceholderText("点击「识别全部字段」查看 OCR 结果")
        layout.addWidget(self._result_text, stretch=1)

        self._status_bar = QStatusBar()
        self._status_bar.showMessage("请先新建或加载布局")
        layout.addWidget(self._status_bar)

    # ─── 布局栏操作 ──────────────────────────────────────

    def _update_layout_label(self):
        """更新当前布局标签：有激活布局显示名称，否则红色「无布局」"""
        name = self._manager.get_active_layout_name()
        if name:
            self._layout_name_label.setText(name)
            self._layout_name_label.setStyleSheet("color: #333; font-weight: bold;")
        else:
            self._layout_name_label.setText("无布局")
            self._layout_name_label.setStyleSheet("color: red; font-weight: bold;")
        # 刷新按钮可用性
        has_layout = self._current_layout is not None
        self._btn_save.setEnabled(has_layout)
        self._btn_save_as.setEnabled(has_layout)
        self._btn_activate.setEnabled(has_layout)
        # 激活布局不可删除
        active = self._manager.get_active_layout_name()
        self._btn_delete.setEnabled(has_layout and name and active != (self._current_layout.name if has_layout else ""))

    def _auto_load_active(self):
        """启动时自动加载激活布局"""
        name = self._manager.get_active_layout_name()
        if name:
            layout = self._manager.load_layout(name)
            if layout:
                self._current_layout = layout
                self._apply_layout_to_tabs()
        self._update_layout_label()

    def _on_activate_layout(self):
        """将当前加载的布局设为激活"""
        if self._current_layout is None:
            self._status_bar.showMessage("没有已加载的布局")
            return
        name = self._current_layout.name
        self._manager.set_active_layout(name)
        self._update_layout_label()
        self._status_bar.showMessage(f"已激活布局「{name}」")

    def _on_new_layout(self):
        """新建空布局"""
        name, ok = QInputDialog.getText(self, "新建布局", "请输入布局名称：")
        if not ok or not name:
            return
        name = name.strip()
        if not name:
            return
        self._current_layout = self._manager.new_layout(name)
        self._apply_layout_to_tabs()
        self._update_layout_label()
        self._status_bar.showMessage(f"已新建布局「{name}」并设为激活")

    def _on_load_layout(self):
        """弹出对话框选择布局并加载"""
        layouts = self._manager.list_layouts()
        if not layouts:
            self._status_bar.showMessage("没有可加载的布局，请先新建")
            return
        name, ok = QInputDialog.getItem(
            self, "加载布局", "选择要加载的布局：", layouts, 0, False,
        )
        if not ok or not name:
            return
        layout = self._manager.load_layout(name)
        if layout is None:
            self._status_bar.showMessage(f"布局「{name}」加载失败")
            return
        self._current_layout = layout
        self._apply_layout_to_tabs()
        self._update_layout_label()
        self._status_bar.showMessage(f"已加载布局「{name}」")

    def _on_save_layout(self):
        """从所有 Tab 收集 regions，全量写入当前布局文件"""
        if self._current_layout is None:
            self._status_bar.showMessage("没有已加载的布局")
            return
        name = self._current_layout.name
        for scene_key, tab in self._tabs.items():
            self._current_layout.set_scene_regions(scene_key, tab.get_regions())
        self._manager.save_layout(self._current_layout)
        self._manager.set_active_layout(name)
        self._update_layout_label()
        total = sum(len(tab.get_regions()) for tab in self._tabs.values())
        self._status_bar.showMessage(f"已保存布局「{name}」，共 {total} 个区域")
        logger.info(f"布局已保存: {name}, {total} 个区域")

    def _on_save_as_layout(self):
        """另存为：输入新名称，若已存在则提示确认覆盖"""
        if self._current_layout is None:
            self._status_bar.showMessage("没有已加载的布局")
            return
        # 收集当前 regions 到临时布局
        temp = Layout(name="")
        for scene_key, tab in self._tabs.items():
            temp.set_scene_regions(scene_key, tab.get_regions())

        existing = self._manager.list_layouts()
        name, ok = QInputDialog.getText(
            self, "另存为", "请输入布局名称：",
        )
        if not ok or not name:
            return
        name = name.strip()
        if not name:
            return

        # 检查是否覆盖
        if name in existing:
            reply = QMessageBox.question(
                self, "确认覆盖",
                f"布局「{name}」已存在，是否覆盖？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        temp.name = name
        self._manager.save_layout(temp)
        self._manager.set_active_layout(name)
        self._current_layout = temp
        self._update_layout_label()
        total = sum(len(r) for r in temp.scenes.values())
        self._status_bar.showMessage(f"已另存为布局「{name}」，共 {total} 个区域")
        logger.info(f"布局已另存为: {name}, {total} 个区域")

    def _on_delete_layout(self):
        """删除布局（激活的不可删除）"""
        active = self._manager.get_active_layout_name()
        layouts = self._manager.list_layouts()
        # 过滤掉激活布局
        deletable = [n for n in layouts if n != active]
        if not deletable:
            self._status_bar.showMessage("没有可删除的布局（激活布局不可删除）")
            return
        name, ok = QInputDialog.getItem(
            self, "删除布局", "选择要删除的布局：", deletable, 0, False,
        )
        if not ok or not name:
            return
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除布局「{name}」吗？此操作不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if self._manager.delete_layout(name):
            # 如果删除的是当前加载的布局，清空内存
            if self._current_layout and self._current_layout.name == name:
                self._current_layout = None
                self._clear_all_tabs()
            self._update_layout_label()
            self._status_bar.showMessage(f"已删除布局「{name}」")
        else:
            self._status_bar.showMessage(f"删除失败：布局「{name}」不存在")

    # ─── Tab 操作 ────────────────────────────────────────

    def _apply_layout_to_tabs(self):
        """将当前布局的区域数据分发到各 Tab"""
        if self._current_layout is None:
            return
        for scene_key, tab in self._tabs.items():
            regions = self._current_layout.get_scene_regions(scene_key)
            tab.set_regions(regions)

    def _clear_all_tabs(self):
        """清空所有 Tab 的区域"""
        for tab in self._tabs.values():
            tab.set_regions([])

    # ─── 刷新截图 ────────────────────────────────────────

    def _on_refresh_image(self):
        """刷新截图（调用外部回调获取新截图）"""
        if self._refresh_callback is None:
            self._status_bar.showMessage("无截图源，请先在主窗口定位窗口")
            return
        new_image = self._refresh_callback()
        if new_image is not None:
            self._image = new_image
            for tab in self._tabs.values():
                tab.canvas.set_image(new_image)
            self._status_bar.showMessage("截图已刷新")
        else:
            self._status_bar.showMessage("刷新截图失败")

    # ─── OCR 识别 ────────────────────────────────────────

    def _on_recognize(self):
        """对当前 Tab 场景的所有已定义区域执行 OCR 识别"""
        current_tab = self._tabs.get(self._current_scene_key)
        if current_tab is None:
            return
        fields = get_scene_fields(current_tab.scene_key)
        regions = current_tab.get_regions()
        if not regions:
            self._status_bar.showMessage("没有已定义的区域")
            return
        if self._image is None:
            self._status_bar.showMessage("没有截图图片")
            return

        self._status_bar.showMessage("正在识别...")
        QApplication.processEvents()

        from ..core.ocr import OCREngine
        engine = OCREngine()
        h, w = self._image.shape[:2]

        self._result_text.clear()
        results = {}
        for region in regions:
            x1 = int(region.x_ratio * w)
            y1 = int(region.y_ratio * h)
            x2 = int((region.x_ratio + region.w_ratio) * w)
            y2 = int((region.y_ratio + region.h_ratio) * h)
            crop = self._image[y1:y2, x1:x2]
            ocr_results = engine.recognize(crop)
            text = " | ".join(r.text for r in ocr_results) if ocr_results else "(未识别到)"
            results[region.name] = text
            self._result_text.append(f"{region.name}: {text}")

        self._status_bar.showMessage(
            f"识别完成，共 {len(results)} 个字段"
        )
        logger.info(
            f"OCR 识别完成 (场景={get_scene_name(current_tab.scene_key)}): {results}"
        )

    @property
    def _current_scene_key(self) -> str:
        """当前 Tab 对应的 scene_key"""
        idx = self._tab_widget.currentIndex()
        keys = list(FIELD_GROUPS.keys())
        return keys[idx] if 0 <= idx < len(keys) else keys[0]
