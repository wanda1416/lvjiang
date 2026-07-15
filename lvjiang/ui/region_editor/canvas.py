"""画布组件 - 可交互的图片画布，支持框选/拖拽/缩放矩形"""

from enum import Enum, auto
import numpy as np
from PyQt6.QtWidgets import QWidget, QInputDialog, QMenu
from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import (
    QImage, QPixmap, QPainter, QPen, QBrush, QColor,
    QMouseEvent, QPaintEvent, QCursor, QFont, QWheelEvent,
)
from loguru import logger

from ...core.region_config import Region, CanvasConfig, EQUIP_FIELDS


# ─── 颜色方案与常量 ──────────────────────────────────────

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
SNAP_PIXELS = 6  # 吸附像素阈值（widget 像素）


class DragMode(Enum):
    NONE = auto()
    DRAWING = auto()       # 正在绘制新矩形
    MOVING = auto()        # 正在移动矩形
    RESIZING = auto()      # 正在缩放矩形


class EditMode(Enum):
    REGION = auto()        # 区域编辑模式（默认）
    CANVAS = auto()        # 画布编辑模式（移动/缩放画布框）


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
        self._original_image: np.ndarray | None = None  # 原始 numpy 数组，供 OCR
        self._img_w = 0  # 原始图片尺寸
        self._img_h = 0

        # 显示区域（图片在 widget 内的偏移和缩放）
        self._display_rect = QRectF()  # 图片显示区域（widget 坐标）
        self._base_scale = 1.0  # 适应窗口的基准缩放
        self._zoom = 1.0        # 用户缩放倍数

        # 区域列表（归一化坐标 0.0~1.0）
        self._regions: list[Region] = []
        self._selected_idx: int = -1  # 当前选中区域索引
        self._field_selected: bool = False  # 是否由右侧字段列表选中（单区域编辑模式）

        # 交互状态
        self._drag_mode = DragMode.NONE
        self._drag_handle: HandlePos | None = None
        self._drag_start = QPointF()   # 鼠标按下位置（归一化）
        self._drag_orig: Region | None = None  # 拖拽前的原始区域

        # 右键拖拽平移
        self._panning = False
        self._pan_start = QPointF()

        # 吸附对齐参考线（归一化坐标，拖拽/拉伸时临时显示）
        self._snap_lines_x: list[float] = []
        self._snap_lines_y: list[float] = []

        # 信号回调
        self.on_region_changed = None  # callable() -> None
        self.on_canvas_changed = None  # callable() -> None

        # 当前场景的字段列表（由外部设置）
        self._current_fields = EQUIP_FIELDS

        # 画布配置（布局级别，由外部设置）
        self._canvas_config = CanvasConfig()
        self._edit_mode = EditMode.REGION  # 当前编辑模式

        # 画布编辑模式的交互状态
        self._canvas_drag_mode = DragMode.NONE
        self._canvas_drag_handle: HandlePos | None = None
        self._canvas_drag_start = QPointF()
        self._canvas_drag_orig: CanvasConfig | None = None

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
        self._original_image = image  # 保存原始 numpy 数组，供 OCR 使用
        self._zoom = 1.0  # 新图片时重置缩放
        self._recalc_display()
        self.update()

    def get_image(self) -> np.ndarray | None:
        """获取原始截图 numpy 数组（供 OCR 裁剪）"""
        return self._original_image

    def clear_image(self):
        """清除背景图片（切换布局时调用）"""
        self._pixmap = None
        self._original_image = None
        self._img_w = 0
        self._img_h = 0
        self._display_rect = QRectF()
        self.update()

    @property
    def pixmap(self) -> QPixmap | None:
        """是否有背景图片"""
        return self._pixmap

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

    def _canvas_rect_widget(self) -> QRectF:
        """画布框的 widget 坐标矩形"""
        c = self._canvas_config
        tl = self._norm_to_widget(c.x_ratio, c.y_ratio)
        br = self._norm_to_widget(c.x_ratio + c.w_ratio, c.y_ratio + c.h_ratio)
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
            # 检查是否点击了已选中的区域
            pos = event.position()
            if self._selected_idx >= 0 and self._selected_idx < len(self._regions):
                r = self._regions[self._selected_idx]
                rect = self._region_rect_widget(r)
                if rect.contains(pos):
                    # 在已选中区域内右键，弹出菜单
                    self._show_context_menu(pos)
                    return
            # 否则开始拖拽平移
            self._panning = True
            self._pan_start = event.position()
            self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            return
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        pos = event.position()

        # ── 画布编辑模式：操作画布框 ──
        if self._edit_mode == EditMode.CANVAS:
            handle = self._hit_canvas_handle(pos)
            if handle is not None:
                self._canvas_drag_mode = DragMode.RESIZING
                self._canvas_drag_handle = handle
                self._canvas_drag_start = pos
                self._canvas_drag_orig = CanvasConfig(
                    self._canvas_config.x_ratio, self._canvas_config.y_ratio,
                    self._canvas_config.w_ratio, self._canvas_config.h_ratio,
                )
                self.update()
                return
            canvas_rect = self._canvas_rect_widget()
            if canvas_rect.contains(pos):
                self._canvas_drag_mode = DragMode.MOVING
                self._canvas_drag_start = pos
                self._canvas_drag_orig = CanvasConfig(
                    self._canvas_config.x_ratio, self._canvas_config.y_ratio,
                    self._canvas_config.w_ratio, self._canvas_config.h_ratio,
                )
                self.update()
                return
            return

        # ── 区域编辑模式（原有逻辑） ──

        if self._field_selected and self._selected_idx >= 0:
            # 右侧字段列表选中：单区域编辑模式
            r = self._regions[self._selected_idx]
            handle = self._hit_handle(r, pos)
            if handle is not None:
                self._drag_mode = DragMode.RESIZING
                self._drag_handle = handle
                self._drag_start = pos
                self._drag_orig = Region(**r.to_dict())
                self._notify_changed()
                self.update()
                return
            rect = self._region_rect_widget(r)
            if rect.contains(pos):
                self._drag_mode = DragMode.MOVING
                self._drag_start = pos
                self._drag_orig = Region(**r.to_dict())
                self._notify_changed()
                self.update()
                return
            # 点击空白：退出单区域模式，回到全局模式
            self._field_selected = False
            self._selected_idx = -1
            self.update()
            # 继续往下走，进入全局模式逻辑

        # 全局调整模式：可以选中/移动/缩放已有区域，或创建新区域
        idx, handle = self._hit_test(pos)
        if idx >= 0:
            # 点击了已有区域
            self._selected_idx = idx
            r = self._regions[idx]
            if handle is not None:
                self._drag_mode = DragMode.RESIZING
                self._drag_handle = handle
            else:
                self._drag_mode = DragMode.MOVING
            self._drag_start = pos
            self._drag_orig = Region(**r.to_dict())
            self._notify_changed()
            self.update()
            return

        # 空白处：创建新区域（坐标转换为画布相对）
        sx, sy = self._widget_to_norm(pos)
        cx, cy = self._screenshot_to_canvas_norm(sx, sy)
        self._drag_mode = DragMode.DRAWING
        self._drag_start = QPointF(cx, cy)
        self._selected_idx = len(self._regions)
        self._regions.append(Region(
            key="", name="新区域",
            x_ratio=cx, y_ratio=cy, w_ratio=0, h_ratio=0,
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

        # ── 画布编辑模式 ──
        if self._edit_mode == EditMode.CANVAS:
            if self._canvas_drag_mode == DragMode.MOVING:
                dx_s, dy_s = self._widget_to_norm(pos)
                dx_s -= self._widget_to_norm(self._canvas_drag_start)[0]
                dy_s -= self._widget_to_norm(self._canvas_drag_start)[1]
                o = self._canvas_drag_orig
                self._canvas_config.x_ratio = max(0.0, min(1.0 - o.w_ratio, o.x_ratio + dx_s))
                self._canvas_config.y_ratio = max(0.0, min(1.0 - o.h_ratio, o.y_ratio + dy_s))
                self.update()
            elif self._canvas_drag_mode == DragMode.RESIZING:
                self._apply_canvas_resize(pos)
                self.update()
            else:
                self._update_canvas_cursor(pos)
            return

        # ── 区域编辑模式 ──

        if self._drag_mode == DragMode.DRAWING:
            sx, sy = self._widget_to_norm(pos)
            cx, cy = self._screenshot_to_canvas_norm(sx, sy)
            r = self._regions[-1]
            x0 = self._drag_start.x()
            y0 = self._drag_start.y()
            r.x_ratio = min(x0, cx)
            r.y_ratio = min(y0, cy)
            r.w_ratio = abs(cx - x0)
            r.h_ratio = abs(cy - y0)
            self.update()

        elif self._drag_mode == DragMode.MOVING:
            dx_n, dy_n = self._widget_to_norm(pos)
            dx_n -= self._widget_to_norm(self._drag_start)[0]
            dy_n -= self._widget_to_norm(self._drag_start)[1]
            r = self._regions[self._selected_idx]
            r.x_ratio = max(0, min(1 - r.w_ratio, self._drag_orig.x_ratio + dx_n))
            r.y_ratio = max(0, min(1 - r.h_ratio, self._drag_orig.y_ratio + dy_n))
            self._apply_move_snap(r)
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

        # ── 画布编辑模式 ──
        if self._edit_mode == EditMode.CANVAS:
            if self._canvas_drag_mode in (DragMode.MOVING, DragMode.RESIZING):
                self._notify_canvas_changed()
            self._canvas_drag_mode = DragMode.NONE
            self._canvas_drag_handle = None
            self._canvas_drag_orig = None
            self.update()
            return

        # ── 区域编辑模式 ──

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
            # 全局模式下移动/缩放后保留选中，以便用户再次点击获取手柄进行拉伸

        # 清除吸附参考线
        self._snap_lines_x = []
        self._snap_lines_y = []
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

        moving_left = h in (HandlePos.LEFT, HandlePos.TOP_LEFT, HandlePos.BOTTOM_LEFT)
        moving_right = h in (HandlePos.RIGHT, HandlePos.TOP_RIGHT, HandlePos.BOTTOM_RIGHT)
        moving_top = h in (HandlePos.TOP, HandlePos.TOP_LEFT, HandlePos.TOP_RIGHT)
        moving_bottom = h in (HandlePos.BOTTOM, HandlePos.BOTTOM_LEFT, HandlePos.BOTTOM_RIGHT)

        if moving_left:
            x1 = min(nx, x2 - 0.01)
        if moving_right:
            x2 = max(nx, x1 + 0.01)
        if moving_top:
            y1 = min(ny, y2 - 0.01)
        if moving_bottom:
            y2 = max(ny, y1 + 0.01)

        # 对正在拖动的边做吸附
        xs, ys = self._collect_snap_targets(self._selected_idx)
        thx, thy = self._snap_threshold_x(), self._snap_threshold_y()
        self._snap_lines_x = []
        self._snap_lines_y = []
        if moving_left:
            t = self._nearest(x1, xs, thx)
            if t is not None and t < x2 - 0.01:
                x1 = t
                self._snap_lines_x.append(t)
        if moving_right:
            t = self._nearest(x2, xs, thx)
            if t is not None and t > x1 + 0.01:
                x2 = t
                self._snap_lines_x.append(t)
        if moving_top:
            t = self._nearest(y1, ys, thy)
            if t is not None and t < y2 - 0.01:
                y1 = t
                self._snap_lines_y.append(t)
        if moving_bottom:
            t = self._nearest(y2, ys, thy)
            if t is not None and t > y1 + 0.01:
                y2 = t
                self._snap_lines_y.append(t)

        r.x_ratio = max(0.0, x1)
        r.y_ratio = max(0.0, y1)
        r.w_ratio = min(1.0, x2) - r.x_ratio
        r.h_ratio = min(1.0, y2) - r.y_ratio

    # ─── 吸附对齐 ───────────────────────────────

    def _snap_threshold_x(self) -> float:
        """x 方向吸附阈值（归一化），基于像素阈值换算"""
        w = self._display_rect.width()
        return SNAP_PIXELS / w if w > 0 else 0.0

    def _snap_threshold_y(self) -> float:
        """y 方向吸附阈值（归一化）"""
        hgt = self._display_rect.height()
        return SNAP_PIXELS / hgt if hgt > 0 else 0.0

    def _collect_snap_targets(self, exclude_idx: int) -> tuple[list[float], list[float]]:
        """收集其他区域的吸附参考线（归一化）
        返回 (xs, ys)：xs 为竖线 x 值（左边/右边/中心），ys 为横线 y 值（上边/下边/中心）
        """
        xs: list[float] = []
        ys: list[float] = []
        for i, r in enumerate(self._regions):
            if i == exclude_idx:
                continue
            xs.extend([r.x_ratio, r.x_ratio + r.w_ratio, r.x_ratio + r.w_ratio / 2])
            ys.extend([r.y_ratio, r.y_ratio + r.h_ratio, r.y_ratio + r.h_ratio / 2])
        return xs, ys

    @staticmethod
    def _nearest(value: float, targets: list[float], threshold: float) -> float | None:
        """在 targets 中找与 value 最接近且在阈值内的值，无则返回 None"""
        best = None
        best_d = threshold
        for t in targets:
            d = abs(value - t)
            if d <= best_d:
                best_d = d
                best = t
        return best

    def _apply_move_snap(self, r: Region):
        """移动时将区域的左/右/中心、上/下/中心向其他区域吸附对齐"""
        xs, ys = self._collect_snap_targets(self._selected_idx)
        thx, thy = self._snap_threshold_x(), self._snap_threshold_y()
        self._snap_lines_x = []
        self._snap_lines_y = []

        # x 方向：左边、右边、中心三条边取最优吸附
        edges_x = [r.x_ratio, r.x_ratio + r.w_ratio, r.x_ratio + r.w_ratio / 2]
        best = None  # (dist, shift, line)
        for e in edges_x:
            t = self._nearest(e, xs, thx)
            if t is not None:
                d = abs(e - t)
                if best is None or d < best[0]:
                    best = (d, t - e, t)
        if best is not None:
            r.x_ratio = max(0.0, min(1 - r.w_ratio, r.x_ratio + best[1]))
            self._snap_lines_x.append(best[2])

        # y 方向
        edges_y = [r.y_ratio, r.y_ratio + r.h_ratio, r.y_ratio + r.h_ratio / 2]
        best = None
        for e in edges_y:
            t = self._nearest(e, ys, thy)
            if t is not None:
                d = abs(e - t)
                if best is None or d < best[0]:
                    best = (d, t - e, t)
        if best is not None:
            r.y_ratio = max(0.0, min(1 - r.h_ratio, r.y_ratio + best[1]))
            self._snap_lines_y.append(best[2])

    def _update_cursor(self, pos: QPointF):
        """根据鼠标位置更新光标"""
        if self._field_selected and self._selected_idx >= 0:
            # 单区域编辑模式：只对选中区域响应
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
            self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
            return

        # 全局调整模式：检测所有区域
        idx, handle = self._hit_test(pos)
        if idx >= 0:
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
            self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
            return
        # 空白处：十字光标（创建模式）
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
            self._field_selected = False
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
            self._selected_idx = -1
            self._field_selected = False  # 创建完成后回到全局模式
        else:
            # 取消则删除该区域
            self._regions.pop(region_idx)
            self._selected_idx = -1
            self._field_selected = False

        self._notify_changed()
        self.update()

    # ─── 公开接口 ────────────────────────────────────────

    def set_regions(self, regions: list[Region]):
        """设置区域列表（从预设加载）"""
        self._regions = [Region(**r.to_dict()) for r in regions]
        self._selected_idx = -1
        self._field_selected = False
        self.update()

    def get_regions(self) -> list[Region]:
        """获取当前区域列表"""
        return [Region(**r.to_dict()) for r in self._regions if r.key]

    def select_region(self, idx: int):
        """从外部（右侧字段列表）选中某区域，进入单区域编辑模式"""
        if 0 <= idx < len(self._regions):
            self._selected_idx = idx
            self._field_selected = True
            self.update()

    def delete_selected(self):
        """删除选中区域"""
        if self._selected_idx >= 0:
            self._regions.pop(self._selected_idx)
            self._selected_idx = -1
            self._field_selected = False
            self._notify_changed()
            self.update()

    def _show_context_menu(self, pos: QPointF):
        """在指定位置显示右键菜单"""
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background-color: #f0f0f0; padding: 4px; }"
            "QMenu::item { padding: 4px 16px; }"
            "QMenu::item:selected { background-color: #ddd; }"
        )
        copy_action = menu.addAction("复制区域")
        delete_action = menu.addAction("删除区域")
        action = menu.exec(self.mapToGlobal(pos.toPoint()))
        if action == copy_action:
            self._copy_selected_region()
        elif action == delete_action:
            self.delete_selected()

    def _copy_selected_region(self):
        """复制选中区域：创建相同大小的新区域，提示绑定新字段，并选中新区域"""
        if self._selected_idx < 0 or self._selected_idx >= len(self._regions):
            return
        src = self._regions[self._selected_idx]
        # 创建新区域，位置相同
        new_region = Region(
            key="", name="新区域",
            x_ratio=src.x_ratio, y_ratio=src.y_ratio,
            w_ratio=src.w_ratio, h_ratio=src.h_ratio,
        )
        self._regions.append(new_region)
        new_idx = len(self._regions) - 1
        # 提示绑定字段
        self._prompt_field_selection(new_idx)
        # 选中新区域（方便用户拖走）
        if self._regions[new_idx].key:  # 如果绑定成功
            self._selected_idx = new_idx
            self._field_selected = True
            self.update()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
        else:
            super().keyPressEvent(event)

    def clear_field_selection(self):
        """清除字段选择，回到全局调整模式"""
        self._selected_idx = -1
        self._field_selected = False
        self.update()

    # ─── 画布编辑模式辅助方法 ───────────────────────

    def _hit_canvas_handle(self, pos: QPointF) -> HandlePos | None:
        """检测是否命中画布框的缩放手柄"""
        rect = self._canvas_rect_widget()
        cx = rect.center().x()
        cy = rect.center().y()
        handles = {
            HandlePos.TOP_LEFT:     rect.topLeft(),
            HandlePos.TOP:          QPointF(cx, rect.top()),
            HandlePos.TOP_RIGHT:    rect.topRight(),
            HandlePos.RIGHT:        QPointF(rect.right(), cy),
            HandlePos.BOTTOM_RIGHT: rect.bottomRight(),
            HandlePos.BOTTOM:       QPointF(cx, rect.bottom()),
            HandlePos.BOTTOM_LEFT:  rect.bottomLeft(),
            HandlePos.LEFT:         QPointF(rect.left(), cy),
        }
        for hpos, center in handles.items():
            hr = QRectF(
                center.x() - HANDLE_SIZE, center.y() - HANDLE_SIZE,
                HANDLE_SIZE * 2, HANDLE_SIZE * 2,
            )
            if hr.contains(pos):
                return hpos
        return None

    def _apply_canvas_resize(self, pos: QPointF):
        """根据手柄位置调整画布框大小"""
        nx, ny = self._widget_to_norm(pos)
        o = self._canvas_drag_orig
        h = self._canvas_drag_handle

        x1, y1 = o.x_ratio, o.y_ratio
        x2, y2 = o.x_ratio + o.w_ratio, o.y_ratio + o.h_ratio

        moving_left = h in (HandlePos.LEFT, HandlePos.TOP_LEFT, HandlePos.BOTTOM_LEFT)
        moving_right = h in (HandlePos.RIGHT, HandlePos.TOP_RIGHT, HandlePos.BOTTOM_RIGHT)
        moving_top = h in (HandlePos.TOP, HandlePos.TOP_LEFT, HandlePos.TOP_RIGHT)
        moving_bottom = h in (HandlePos.BOTTOM, HandlePos.BOTTOM_LEFT, HandlePos.BOTTOM_RIGHT)

        if moving_left:
            x1 = min(nx, x2 - 0.01)
        if moving_right:
            x2 = max(nx, x1 + 0.01)
        if moving_top:
            y1 = min(ny, y2 - 0.01)
        if moving_bottom:
            y2 = max(ny, y1 + 0.01)

        self._canvas_config.x_ratio = max(0.0, x1)
        self._canvas_config.y_ratio = max(0.0, y1)
        self._canvas_config.w_ratio = min(1.0, x2) - self._canvas_config.x_ratio
        self._canvas_config.h_ratio = min(1.0, y2) - self._canvas_config.y_ratio

    def _update_canvas_cursor(self, pos: QPointF):
        """画布模式下更新鼠标光标"""
        handle = self._hit_canvas_handle(pos)
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
        canvas_rect = self._canvas_rect_widget()
        if canvas_rect.contains(pos):
            self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
            return
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

    # ─── 模式切换 ───────────────────────────────────

    def set_canvas_mode(self):
        """切换到画布编辑模式"""
        self._edit_mode = EditMode.CANVAS
        self._selected_idx = -1
        self._field_selected = False
        self.update()

    def set_region_mode(self):
        """切换到区域编辑模式"""
        self._edit_mode = EditMode.REGION
        self.update()

    @property
    def edit_mode(self) -> EditMode:
        return self._edit_mode

    # ─── 画布配置访问 ───────────────────────────────

    def set_canvas_config(self, config: CanvasConfig):
        """设置画布配置（由外部调用）"""
        self._canvas_config = config
        self.update()

    def get_canvas_config(self) -> CanvasConfig:
        """获取当前画布配置"""
        return CanvasConfig(
            self._canvas_config.x_ratio, self._canvas_config.y_ratio,
            self._canvas_config.w_ratio, self._canvas_config.h_ratio,
        )

    def _notify_canvas_changed(self):
        if self.on_canvas_changed:
            self.on_canvas_changed()

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
        else:
            # 无图片时绘制占位提示
            self._draw_placeholder(painter)

        # 绘制画布框（遮罩 + 边框）
        self._draw_canvas_frame(painter)

        # 绘制区域
        if self._field_selected and self._selected_idx >= 0 and self._selected_idx < len(self._regions):
            # 单区域编辑模式：只显示选中区域
            r = self._regions[self._selected_idx]
            color = REGION_COLORS[self._selected_idx % len(REGION_COLORS)]
            self._draw_region(painter, r, color, True)
        else:
            # 全局调整模式：显示所有区域，选中的高亮
            for i, r in enumerate(self._regions):
                color = REGION_COLORS[i % len(REGION_COLORS)]
                is_sel = (i == self._selected_idx and not self._field_selected)
                self._draw_region(painter, r, color, is_sel)

        # 吸附对齐参考线（拖拽/拉伸时临时显示）
        if self._snap_lines_x or self._snap_lines_y:
            painter.save()
            painter.setPen(QPen(QColor(255, 0, 255), 1, Qt.PenStyle.DashLine))
            for cx in self._snap_lines_x:
                # 画布相对归一化 -> 截图归一化 -> widget 坐标
                sx, _ = self._canvas_to_screenshot_norm(cx, 0)
                x = self._display_rect.x() + sx * self._display_rect.width()
                painter.drawLine(
                    QPointF(x, self._display_rect.top()),
                    QPointF(x, self._display_rect.bottom()),
                )
            for cy in self._snap_lines_y:
                # 画布相对归一化 -> 截图归一化 -> widget 坐标
                _, sy = self._canvas_to_screenshot_norm(0, cy)
                y = self._display_rect.y() + sy * self._display_rect.height()
                painter.drawLine(
                    QPointF(self._display_rect.left(), y),
                    QPointF(self._display_rect.right(), y),
                )
            painter.restore()

        painter.end()

    def _draw_placeholder(self, painter: QPainter):
        """无图片时绘制占位提示"""
        painter.save()
        # 深灰背景
        painter.fillRect(self.rect(), QColor(40, 40, 40))
        # 居中提示文字
        font = QFont("Microsoft YaHei", 12)
        painter.setFont(font)
        painter.setPen(QColor(150, 150, 150))
        text = "无截图\n请连接投屏后点击「刷新截图」"
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def _draw_canvas_frame(self, painter: QPainter):
        """绘制画布框：遮罩 + 边框 + 手柄"""
        c = self._canvas_config
        # 如果画布覆盖全图，只绘制边框提示
        is_full = (c.x_ratio <= 0.001 and c.y_ratio <= 0.001
                   and c.w_ratio >= 0.998 and c.h_ratio >= 0.998)

        canvas_rect = self._canvas_rect_widget()

        if not is_full:
            # 画布外半透明遮罩
            painter.save()
            dr = self._display_rect
            # 上
            painter.fillRect(QRectF(dr.left(), dr.top(), dr.width(), canvas_rect.top() - dr.top()),
                             QColor(0, 0, 0, 120))
            # 下
            painter.fillRect(QRectF(dr.left(), canvas_rect.bottom(), dr.width(), dr.bottom() - canvas_rect.bottom()),
                             QColor(0, 0, 0, 120))
            # 左
            painter.fillRect(QRectF(dr.left(), canvas_rect.top(), canvas_rect.left() - dr.left(), canvas_rect.height()),
                             QColor(0, 0, 0, 120))
            # 右
            painter.fillRect(QRectF(canvas_rect.right(), canvas_rect.top(), dr.right() - canvas_rect.right(), canvas_rect.height()),
                             QColor(0, 0, 0, 120))
            painter.restore()

        # 画布边框（始终清晰可见）
        painter.save()
        is_canvas_mode = self._edit_mode == EditMode.CANVAS
        pen_color = QColor(255, 200, 0) if is_canvas_mode else QColor(255, 200, 0, 200)
        pen_width = 3 if is_canvas_mode else 2
        # 先画黑色底衬，确保在任何背景下都可辨
        painter.setPen(QPen(QColor(0, 0, 0, 180), pen_width + 2, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(canvas_rect)
        # 再画黄色虚线
        painter.setPen(QPen(pen_color, pen_width, Qt.PenStyle.DashLine))
        painter.drawRect(canvas_rect)
        painter.restore()

        # 画布编辑模式下绘制手柄
        if self._edit_mode == EditMode.CANVAS:
            painter.save()
            cx = canvas_rect.center().x()
            cy = canvas_rect.center().y()
            handles = {
                HandlePos.TOP_LEFT:     canvas_rect.topLeft(),
                HandlePos.TOP:          QPointF(cx, canvas_rect.top()),
                HandlePos.TOP_RIGHT:    canvas_rect.topRight(),
                HandlePos.RIGHT:        QPointF(canvas_rect.right(), cy),
                HandlePos.BOTTOM_RIGHT: canvas_rect.bottomRight(),
                HandlePos.BOTTOM:       QPointF(cx, canvas_rect.bottom()),
                HandlePos.BOTTOM_LEFT:  canvas_rect.bottomLeft(),
                HandlePos.LEFT:         QPointF(canvas_rect.left(), cy),
            }
            painter.setPen(QPen(QColor(255, 200, 0), 1))
            painter.setBrush(QBrush(QColor(255, 200, 0)))
            for center in handles.values():
                painter.drawRect(
                    QRectF(
                        center.x() - HANDLE_SIZE, center.y() - HANDLE_SIZE,
                        HANDLE_SIZE * 2, HANDLE_SIZE * 2,
                    )
                )
            painter.restore()

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
            painter.save()
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
            painter.restore()

    # ─── 尺寸变化 ────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 窗口大小变化时，重置缩放并重新计算显示区域
        if self._pixmap:
            self._zoom = 1.0
            self._recalc_display()

    # ─── 通知 ────────────────────────────────────────────

    def _notify_changed(self):
        if self.on_region_changed:
            self.on_region_changed()
