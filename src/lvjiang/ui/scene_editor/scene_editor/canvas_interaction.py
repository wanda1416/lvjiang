"""画布交互逻辑混入类 - 鼠标事件、命中检测、拖拽缩放、吸附对齐"""

from enum import Enum, auto
from typing import Callable

from loguru import logger
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QCursor, QMouseEvent, QWheelEvent
from PyQt6.QtWidgets import QInputDialog, QMenu

from ....core.scene_registry import CanvasConfig, Panel, Region
from .canvas_coords import CanvasCoordMixin

# ─── 枚举定义 ────────────────────────────────────────────

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


HANDLE_SIZE = 6  # 缩放手柄半尺寸
SNAP_PIXELS = 6  # 吸附像素阈值（widget 像素）

# 手柄光标映射
HANDLE_CURSORS = {
    HandlePos.TOP_LEFT:     Qt.CursorShape.SizeFDiagCursor,
    HandlePos.BOTTOM_RIGHT: Qt.CursorShape.SizeFDiagCursor,
    HandlePos.TOP_RIGHT:    Qt.CursorShape.SizeBDiagCursor,
    HandlePos.BOTTOM_LEFT:  Qt.CursorShape.SizeBDiagCursor,
    HandlePos.TOP:          Qt.CursorShape.SizeVerCursor,
    HandlePos.BOTTOM:       Qt.CursorShape.SizeVerCursor,
    HandlePos.LEFT:         Qt.CursorShape.SizeHorCursor,
    HandlePos.RIGHT:        Qt.CursorShape.SizeHorCursor,
}


class CanvasInteractionMixin(CanvasCoordMixin):
    """交互逻辑混入类 - 需要主类提供状态属性和绘制方法"""

    # 以下属性由主类提供
    _regions: list[Region]
    _selected_idx: int
    _field_selected: bool
    _drag_mode: DragMode
    _drag_handle: HandlePos | None
    _drag_start: QPointF
    _drag_orig: Region | None
    _panning: bool
    _pan_start: QPointF
    _snap_lines_x: list[float]
    _snap_lines_y: list[float]
    _current_regions: list[tuple[str, str]]
    _edit_mode: EditMode
    _canvas_drag_mode: DragMode
    _canvas_drag_handle: HandlePos | None
    _canvas_drag_start: QPointF
    _canvas_drag_orig: CanvasConfig | None
    on_region_changed: Callable | None
    on_canvas_changed: Callable | None

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

    # ─── 吸附对齐 ────────────────────────────────────────

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

    # ─── 鼠标事件 ────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.RightButton:
            pos = event.position()
            # Panel 右键菜单优先：命中已选中 panel 则弹出复制/删除
            if self._panel_selected_idx >= 0 and self._panel_selected_idx < len(self._panels):
                p = self._panels[self._panel_selected_idx]
                rect = self._panel_rect_widget(p)
                if rect.contains(pos):
                    self._show_panel_context_menu(pos)
                    return
            # POI（point）右键菜单：命中坐标点则弹出复制/删除
            if self._edit_mode == EditMode.REGION and self._poi_handle_context_menu(pos):
                return
            # Region 右键菜单：命中已选中区域则弹出
            if self._selected_idx >= 0 and self._selected_idx < len(self._regions):
                r = self._regions[self._selected_idx]
                rect = self._region_rect_widget(r)
                if rect.contains(pos):
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

        # ── Panel 放置模式优先介入 ──
        if self._pending_panel_def is not None:
            self._panel_drag_start = pos
            self._panel_drag_current = pos
            self.update()
            return

        # ── Panel 移动/缩放介入 ──
        if self._edit_mode == EditMode.REGION and self._panels:
            p_idx, p_handle = self._hit_panel_test(pos)
            if p_idx >= 0:
                self._panel_selected_idx = p_idx
                if p_handle is not None:
                    self._panel_edit_mode = DragMode.RESIZING
                    self._panel_edit_handle = p_handle
                else:
                    self._panel_edit_mode = DragMode.MOVING
                self._panel_edit_start = pos
                p = self._panels[p_idx]
                self._panel_edit_orig = Panel(
                    key=p.key, x_ratio=p.x_ratio, y_ratio=p.y_ratio,
                    w_ratio=p.w_ratio, h_ratio=p.h_ratio,
                    cols=p.cols, rows=p.rows, min_visible=p.min_visible,
                )
                # 仅选中，数据未变 → 不能标记 dirty
                self._notify_selection_changed()
                self.update()
                return
            # 点击空白处取消 panel 选中
            if self._panel_selected_idx >= 0:
                self._panel_selected_idx = -1
                self._notify_selection_changed()
                self.update()

        # ── POI（point/arrow）优先介入 ──
        if self._edit_mode == EditMode.REGION and self._poi_handle_press(event):
            return

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
                self.update()
                return
            rect = self._region_rect_widget(r)
            if rect.contains(pos):
                self._drag_mode = DragMode.MOVING
                self._drag_start = pos
                self._drag_orig = Region(**r.to_dict())
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
            self.update()
            return

        # 空白处：创建新区域（坐标转换为画布相对）
        sx, sy = self._widget_to_norm(pos)
        cx, cy = self._screenshot_to_canvas_norm(sx, sy)
        self._drag_mode = DragMode.DRAWING
        self._drag_start = QPointF(cx, cy)
        self._selected_idx = len(self._regions)
        self._regions.append(Region(
            key="",
            x_ratio=cx, y_ratio=cy, w_ratio=0, h_ratio=0,
        ))
        # 刚开始框选，尚未产生有效数据（释放时才绑定字段），不标记 dirty
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

        # ── Panel 放置模式拖拽预览 ──
        if self._panel_drag_start is not None:
            self._panel_drag_current = pos
            self.update()
            return

        # ── Panel 移动/缩放拖拽 ──
        if self._panel_edit_mode is not None and self._panel_edit_orig is not None:
            p = self._panels[self._panel_selected_idx]
            o = self._panel_edit_orig
            dx_n, dy_n = self._widget_to_norm(pos)
            dx_n -= self._widget_to_norm(self._panel_edit_start)[0]
            dy_n -= self._widget_to_norm(self._panel_edit_start)[1]
            if self._panel_edit_mode == DragMode.MOVING:
                p.x_ratio = max(0, min(1 - p.w_ratio, o.x_ratio + dx_n))
                p.y_ratio = max(0, min(1 - p.h_ratio, o.y_ratio + dy_n))
            elif self._panel_edit_mode == DragMode.RESIZING:
                self._apply_panel_resize(p, o, pos)
            self._notify_panel_changed()
            self.update()
            return

        # ── POI（point/arrow）优先介入 ──
        if self._edit_mode == EditMode.REGION and self._poi_handle_move(event):
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
            # Shift 按下时禁用吸附
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self._snap_lines_x = []
                self._snap_lines_y = []
            else:
                self._apply_move_snap(r)
            self.update()

        elif self._drag_mode == DragMode.RESIZING:
            self._apply_resize(pos, event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
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

        # ── Panel 放置模式完成 ──
        if self._panel_drag_start is not None and self._pending_panel_def is not None:
            end_pos = event.position()
            # widget 坐标 -> 截图归一化 -> 画布归一化
            sx0, sy0 = self._widget_to_norm(self._panel_drag_start)
            sx1, sy1 = self._widget_to_norm(end_pos)
            cx0, cy0 = self._screenshot_to_canvas_norm(sx0, sy0)
            cx1, cy1 = self._screenshot_to_canvas_norm(sx1, sy1)
            x = min(cx0, cx1)
            y = min(cy0, cy1)
            w = abs(cx1 - cx0)
            h = abs(cy1 - cy0)
            # 矩形太小则忽略
            if w >= 0.01 and h >= 0.01:
                from ....core.scene_registry import Panel
                pd = self._pending_panel_def
                panel = Panel(
                    key=pd.key,
                    x_ratio=x, y_ratio=y, w_ratio=w, h_ratio=h,
                    cols=pd.cols, rows=pd.rows,
                    min_visible=getattr(pd, "min_visible", 0.95),
                )
                self._panels.append(panel)
                self._panel_selected_idx = len(self._panels) - 1
                self._notify_panel_changed()
            self.cancel_panel_place()
            self.update()
            return

        # ── Panel 移动/缩放完成 ──
        if self._panel_edit_mode is not None:
            # 与拖拽前备份比对：纯点击（未移动）不算数据变更
            moved = False
            if (self._panel_edit_orig is not None
                    and 0 <= self._panel_selected_idx < len(self._panels)):
                p = self._panels[self._panel_selected_idx]
                moved = p.to_dict() != self._panel_edit_orig.to_dict()
            self._panel_edit_mode = None
            self._panel_edit_handle = None
            self._panel_edit_orig = None
            if moved:
                self._notify_panel_changed()
            else:
                self._notify_selection_changed()
            self.update()
            return

        # ── POI（point/arrow）优先介入 ──
        if self._edit_mode == EditMode.REGION and self._poi_handle_release(event):
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
            # 与拖拽前备份比对：纯点击选中（几何未变）不算数据变更
            changed = False
            if (self._drag_orig is not None
                    and 0 <= self._selected_idx < len(self._regions)):
                r = self._regions[self._selected_idx]
                changed = r.to_dict() != self._drag_orig.to_dict()
            if changed:
                self._notify_changed()
            else:
                self._notify_selection_changed()
            # 全局模式下移动/缩放后保留选中，以便用户再次点击获取手柄进行拉伸

        # 清除吸附参考线
        self._snap_lines_x = []
        self._snap_lines_y = []
        self._drag_mode = DragMode.NONE
        self._drag_handle = None
        self._drag_orig = None
        self.update()

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

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            # 取消 panel 放置模式
            if self._pending_panel_def is not None:
                self.cancel_panel_place()
                return
            if self._poi_handle_escape():
                return
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
        else:
            super().keyPressEvent(event)

    # ─── 拖拽缩放 ────────────────────────────────────────

    def _apply_resize(self, pos: QPointF, shift_held: bool = False):
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

        # Shift 按下时禁用吸附
        self._snap_lines_x = []
        self._snap_lines_y = []
        if not shift_held:
            # 对正在拖动的边做吸附
            xs, ys = self._collect_snap_targets(self._selected_idx)
            thx, thy = self._snap_threshold_x(), self._snap_threshold_y()
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

    # ─── 光标管理 ────────────────────────────────────────

    def _update_cursor(self, pos: QPointF):
        """根据鼠标位置更新光标"""
        # Panel 光标优先（仅在区域编辑模式下）
        if self._edit_mode == EditMode.REGION and self._panels:
            p_idx, p_handle = self._hit_panel_test(pos)
            if p_idx >= 0:
                if p_handle is not None:
                    self.setCursor(QCursor(HANDLE_CURSORS[p_handle]))
                    return
                self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
                return

        if self._field_selected and self._selected_idx >= 0:
            # 单区域编辑模式：只对选中区域响应
            r = self._regions[self._selected_idx]
            handle = self._hit_handle(r, pos)
            if handle is not None:
                self.setCursor(QCursor(HANDLE_CURSORS[handle]))
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
                self.setCursor(QCursor(HANDLE_CURSORS[handle]))
                return
            self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
            return
        # 空白处：十字光标（创建模式）
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))

    def _update_canvas_cursor(self, pos: QPointF):
        """画布模式下更新鼠标光标"""
        handle = self._hit_canvas_handle(pos)
        if handle is not None:
            self.setCursor(QCursor(HANDLE_CURSORS[handle]))
            return
        canvas_rect = self._canvas_rect_widget()
        if canvas_rect.contains(pos):
            self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
            return
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))

    # ─── 区域选择与右键菜单 ──────────────────────────────

    def _prompt_field_selection(self, region_idx: int):
        """弹出区域选择对话框"""
        # 已绑定集合必须含被视图过滤隐藏的实例（_hidden_regions），
        # 否则在非基底视图下新建区域时，其他视图已绑定的区域会被当成未绑定重复出现
        new_region = self._regions[region_idx]
        assigned = {
            r.key for r in (self._regions + self._hidden_regions)
            if r is not new_region and r.key
        }
        # 候选仅限当前视图可见的字段（基底 + 当前视图，即 _visible_keys）：
        # 选定非基底视图时不应允许绑定当前不可见视图的字段（_visible_keys 为 None = 看全部）
        available = [
            (k, n) for k, n in self._current_regions
            if k not in assigned
            and (self._visible_keys is None or k in self._visible_keys)
        ]
        logger.debug(f"区域选择: current_regions={self._current_regions}, available={available}")

        if not available:
            msg = "当前视图下没有可绑定的区域字段（已全部分配或不属于本视图）"
            logger.warning(msg)
            self._notify_status(msg)
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
            self._selected_idx = -1
            self._field_selected = False  # 创建完成后回到全局模式
            self._notify_changed()
        else:
            # 取消则删除该区域：数据回到原状，不标记 dirty
            self._regions.pop(region_idx)
            self._selected_idx = -1
            self._field_selected = False
            self._notify_selection_changed()

        self.update()

    def _show_context_menu(self, pos: QPointF):
        """在指定位置显示右键菜单"""
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background-color: #f0f0f0; padding: 4px; }"
            "QMenu::item { padding: 4px 16px; }"
            "QMenu::item:selected { background-color: #ddd; }"
        )
        copy_action = menu.addAction("复制")
        delete_action = menu.addAction("删除")
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
            key="",
            x_ratio=src.x_ratio, y_ratio=src.y_ratio,
            w_ratio=src.w_ratio, h_ratio=src.h_ratio,
        )
        self._regions.append(new_region)
        new_idx = len(self._regions) - 1
        # 提示绑定字段（如果所有字段已分配，区域会被移除）
        self._prompt_field_selection(new_idx)
        # 选中新区域（需检查区域是否仍存在）
        if new_idx < len(self._regions) and self._regions[new_idx].key:
            self._selected_idx = new_idx
            self._field_selected = True
            self.update()

    # ─── Panel 右键菜单 ─────────────────────────────────

    def _show_panel_context_menu(self, pos: QPointF):
        """在指定位置显示 Panel 右键菜单"""
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background-color: #f0f0f0; padding: 4px; }"
            "QMenu::item { padding: 4px 16px; }"
            "QMenu::item:selected { background-color: #ddd; }"
        )
        copy_action = menu.addAction("复制 DSL 引用")
        delete_action = menu.addAction("删除面板")
        action = menu.exec(self.mapToGlobal(pos.toPoint()))
        if action == copy_action:
            self._copy_panel_key()
        elif action == delete_action:
            self._delete_selected_panel()

    def _copy_panel_key(self):
        """复制选中 Panel 的 DSL 引用到剪贴板"""
        if self._panel_selected_idx < 0 or self._panel_selected_idx >= len(self._panels):
            return
        panel = self._panels[self._panel_selected_idx]
        from PyQt6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        # 格式: [scene_key].[panel_key]
        if self._scene_key:
            clipboard.setText(f"[{self._scene_key}].[{panel.key}]")
        else:
            clipboard.setText(f"[{panel.key}]")

    def _delete_selected_panel(self):
        """删除选中的 Panel 实例（从布局中解绑）"""
        if self._panel_selected_idx < 0 or self._panel_selected_idx >= len(self._panels):
            return
        del self._panels[self._panel_selected_idx]
        self._panel_selected_idx = -1
        self._notify_panel_changed()
        self.update()

    # ─── 通知 ────────────────────────────────────────────

    def _notify_changed(self):
        if self.on_region_changed:
            self.on_region_changed()

    def _notify_status(self, message: str):
        """向对话框状态栏推送面向用户的提示（无回调时静默）"""
        if self.on_status_message:
            self.on_status_message(message)

    def _notify_canvas_changed(self):
        if self.on_canvas_changed:
            self.on_canvas_changed()

    def _notify_selection_changed(self):
        """仅选中态变化（刷新列表高亮用），不代表数据修改"""
        if self.on_selection_changed:
            self.on_selection_changed()
