"""画布 point / arrow 混入类 - 坐标点与方向的状态、渲染、命中检测、交互

与 region 解耦：region 相关逻辑保持在 canvas_interaction.py 不变，
本混入通过 _poi_handle_press / _poi_handle_move / _poi_handle_release
在鼠标事件早期介入，返回 True 表示已消费该事件。
"""

import math
import re
import time
from enum import Enum, auto

from loguru import logger
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QCursor, QFont, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import QInputDialog, QMenu, QMessageBox

from ...core.layout_models import Arrow, Point
from ...core.scene_registry import get_point_name
from ...i18n import tr

# ─── 常量 ────────────────────────────────────────────────

POINT_HANDLE_SIZE = 5          # 半径调整手柄半尺寸（widget 像素）
ARROW_SNAP_PIXELS = 10         # arrow 终点吸附像素阈值
ARROW_SNAP_STILL_MS = 200      # 停手判定：鼠标静止多少毫秒后才允许吸附
PLUS_BTN_RADIUS = 9            # point 右上角 + 按钮半径（widget 像素）
PLUS_BTN_OFFSET = 4            # + 按钮与圆环的额外间距

POINT_COLOR = QColor(255, 210, 60)          # 坐标点主色（黄）
POINT_FILL = QColor(255, 210, 60, 60)       # 坐标点填充

ARROW_COLORS = [
    QColor(255, 87, 87),     # 红
    QColor(87, 157, 255),    # 蓝
    QColor(87, 255, 157),    # 绿
    QColor(255, 197, 87),    # 橙
    QColor(197, 87, 255),    # 紫
    QColor(87, 255, 255),    # 青
    QColor(255, 87, 197),    # 粉
    QColor(157, 255, 87),    # 黄绿
]

# arrow key 命名规则：id 类
_RE_ARROW_KEY = re.compile(r"^[a-z][a-z0-9_]*$")


class PoiAction(Enum):
    NONE = auto()
    PLACE_POINT = auto()   # 等待在画布上单击落点（_pending_point_key 已知）
    DRAW_ARROW = auto()    # 正在从 _arrow_from_key 画箭头


class PoiDrag(Enum):
    NONE = auto()
    MOVE_POINT = auto()    # 移动 point 中心
    RESIZE_POINT = auto()  # 调整 point 半径
    MOVE_ARROW_END = auto()  # 拖动 arrow 绝对态终点


class CanvasPoiMixin:
    """point / arrow 状态 + 渲染 + 交互混入"""

    # 由主类提供的属性（见 canvas.py __init__）
    _points: list
    _arrows: list
    _current_points: list          # [(key, name), ...] 来自 YAML
    _selected_point_idx: int
    _selected_arrow_idx: int
    _poi_action: PoiAction
    _poi_drag: PoiDrag
    _pending_point_key: str
    _pending_point_name: str
    _pending_point_r: float
    _arrow_from_key: str
    _poi_cursor: QPointF           # 落点预览 / 画箭头时鼠标 widget 位置
    _arrow_snap_idx: int           # 停手吸附命中的 point 索引，-1 无
    _arrow_last_move_ms: float
    _poi_drag_orig: object

    # ─── 初始化 POI 状态（由主类 __init__ 调用） ──────────

    def _init_poi_state(self):
        self._points = []
        self._arrows = []
        # 被视图过滤隐藏的实例（数据仍保留，get_* 时一并返回）
        self._hidden_points = []
        self._hidden_arrows = []
        self._current_points = []
        self._selected_point_idx = -1
        self._selected_arrow_idx = -1
        self._poi_action = PoiAction.NONE
        self._poi_drag = PoiDrag.NONE
        self._pending_point_key = ""
        self._pending_point_name = ""
        self._pending_point_r = 0.015
        self._arrow_from_key = ""
        self._poi_cursor = QPointF()
        self._arrow_snap_idx = -1
        self._arrow_last_move_ms = 0.0
        self._poi_drag_orig = None
        self._poi_drag_moved = False
        # 通知回调：point/arrow 数据变化
        self.on_poi_changed = None

    # ─── 数据访问 ────────────────────────────────────────

    def set_current_points(self, points: list):
        """设置当前场景的坐标点类型列表（来自 YAML，[(key,name),...]）"""
        self._current_points = list(points)

    def set_points(self, points: list):
        self._points, self._hidden_points = self._split_by_filter(
            [point.clone() for point in points]
        )
        self._selected_point_idx = -1
        self.update()

    def get_points(self) -> list:
        """全部坐标点（含被视图过滤隐藏的，保存布局时不能写丢）"""
        return [
            point.clone()
            for point in self._points + self._hidden_points
        ]

    def set_arrows(self, arrows: list):
        self._arrows, self._hidden_arrows = self._split_arrows(
            [arrow.clone() for arrow in arrows]
        )
        self._selected_arrow_idx = -1
        self.update()

    def get_arrows(self) -> list:
        """全部方向（含被视图过滤隐藏的）"""
        return [
            arrow.clone()
            for arrow in self._arrows + self._hidden_arrows
        ]

    def get_visible_arrows(self) -> list:
        """当前视图下可见的方向（供列表展示）"""
        return [arrow.clone() for arrow in self._arrows]

    def _split_arrows(self, arrows: list) -> tuple[list, list]:
        """方向没有独立的视图归属，跟随其起点坐标点的可见性

        注意：依赖 _points 已按视图拆分完毕，故 set_points 必须先于 set_arrows 调用。
        """
        if self._visible_keys is None:
            return list(arrows), []
        visible_pt_keys = {p.key for p in self._points}
        visible: list[Point] = []
        hidden: list[Point] = []
        for a in arrows:
            (visible if a.from_key in visible_pt_keys else hidden).append(a)
        return visible, hidden

    def _apply_poi_filter(self):
        """视图切换后重新拆分 point / arrow（由 set_view_filter 调用）"""
        self._points, self._hidden_points = self._split_by_filter(
            self._points + self._hidden_points
        )
        self._arrows, self._hidden_arrows = self._split_arrows(
            self._arrows + self._hidden_arrows
        )
        self._selected_point_idx = -1
        self._selected_arrow_idx = -1

    def _resolve_point(self, key: str) -> Point | None:
        return next(
            (p for p in self._points + self._hidden_points if p.key == key), None
        )

    def _point_name(self, key: str) -> str:
        for k, n in self._current_points:
            if k == key:
                return n
        # 跨场景引用的点不在本场景 points 里，名字要回源场景取，否则画布上
        # 只有它一个标着 key。
        return get_point_name(self._scene_key, key)

    def _notify_poi_changed(self):
        if self.on_poi_changed:
            self.on_poi_changed()

    # ─── 外部触发：进入创建模式 ───────────────────────────

    def begin_place_point(self, point_key: str, point_name: str = "", r_ratio: float = 0.015):
        """进入落点模式：等待在画布上单击放置指定 point"""
        self._poi_action = PoiAction.PLACE_POINT
        self._pending_point_key = point_key
        self._pending_point_name = point_name or self._point_name(point_key)
        self._pending_point_r = r_ratio
        self._poi_cursor = QPointF()
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.update()

    def begin_draw_arrow(self, from_point_key: str):
        """进入画箭头模式：以指定 point 为起点"""
        point = self._resolve_point(from_point_key)
        if point is None:
            logger.warning(f"起点 point 未放置坐标，无法创建方向: {from_point_key}")
            return
        if point.is_reference:
            logger.warning(f"引用 point 只读，无法创建方向: {from_point_key}")
            return
        self._poi_action = PoiAction.DRAW_ARROW
        self._arrow_from_key = from_point_key
        self._selected_point_idx = -1
        self._arrow_snap_idx = -1
        self._arrow_last_move_ms = time.monotonic() * 1000
        self._poi_cursor = QPointF()
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.update()

    def cancel_poi_action(self):
        """取消当前 POI 创建动作"""
        self._poi_action = PoiAction.NONE
        self._pending_point_key = ""
        self._arrow_from_key = ""
        self._arrow_snap_idx = -1
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.update()

    def clear_poi_selection(self):
        self._selected_point_idx = -1
        self._selected_arrow_idx = -1
        self.update()

    def select_point_by_key(self, key: str):
        for i, p in enumerate(self._points):
            if p.key == key:
                self._selected_point_idx = i
                self._selected_arrow_idx = -1
                self.update()
                return

    def select_arrow_by_key(self, key: str):
        for i, a in enumerate(self._arrows):
            if a.key == key:
                self._selected_arrow_idx = i
                self._selected_point_idx = -1
                self.update()
                return

    def selected_point_key(self) -> str | None:
        """当前选中的已放置 point 的 key，无则 None"""
        if 0 <= self._selected_point_idx < len(self._points):
            return self._points[self._selected_point_idx].key
        return None

    # ─── 删除约束 ────────────────────────────────────────

    def _point_referenced_by_arrow(self, point_key: str) -> bool:
        return any(
            a.from_key == point_key or a.to_key == point_key
            for a in self._arrows + self._hidden_arrows
        )

    def delete_point_by_key(self, key: str) -> bool:
        """删除 point；被 arrow 引用时拒绝并返回 False"""
        point = self._resolve_point(key)
        if point is not None and point.is_reference:
            return False
        if self._point_referenced_by_arrow(key):
            QMessageBox.warning(self.window(), tr("无法删除"),
                                tr("该坐标被方向引用，请先删除关联的方向。"))
            return False
        self._points = [p for p in self._points if p.key != key]
        self._selected_point_idx = -1
        self._notify_poi_changed()
        self.update()
        return True

    def delete_arrow_by_key(self, key: str) -> bool:
        self._arrows = [a for a in self._arrows if a.key != key]
        self._selected_arrow_idx = -1
        self._notify_poi_changed()
        self.update()
        return True

    def rename_arrow_by_key(self, old_key: str, new_key: str) -> bool:
        for a in self._arrows:
            if a.key == old_key:
                a.key = new_key
                self._notify_poi_changed()
                self.update()
                return True
        return False

    # ─── point 右键菜单 ──────────────────────────────────

    def _poi_handle_context_menu(self, pos: QPointF) -> bool:
        """右键命中 point 时弹出「复制坐标 / 删除坐标」菜单，返回 True 表示已消费"""
        idx = self._hit_point(pos)
        if idx < 0:
            return False
        self._selected_point_idx = idx
        self._selected_arrow_idx = -1
        self.update()

        if self._points[idx].is_reference:
            return True

        menu = QMenu(self)  # type: ignore[call-overload]
        menu.setStyleSheet(
            "QMenu { background-color: palette(base); padding: 4px; }"
            "QMenu::item { padding: 4px 16px; }"
            "QMenu::item:selected { background-color: #ddd; }"
        )
        copy_action = menu.addAction(tr("复制"))
        del_action = menu.addAction(tr("删除"))
        action = menu.exec(self.mapToGlobal(pos.toPoint()))
        if action == copy_action:
            self._copy_selected_point()
        elif action == del_action:
            self.delete_point_by_key(self._points[idx].key)
        return True

    def _copy_selected_point(self):
        """复制选中坐标：沿用源半径，绑定一个未放置的 YAML point key，稍作偏移放置"""
        if not (0 <= self._selected_point_idx < len(self._points)):
            return
        src = self._points[self._selected_point_idx]
        if src.is_reference:
            return
        # 已放置集合必须含被视图过滤隐藏的实例，否则其他视图已绑定的点会被当成未绑定
        placed = {p.key for p in self._points + self._hidden_points}
        available = [(k, n) for k, n in self._current_points if k not in placed]
        if not available:
            QMessageBox.information(self.window(), tr("无可用坐标"),
                                    tr("该场景所有 YAML 坐标点都已放置，无法再复制新坐标。"))
            return
        items = [f"{n} ({k})" for k, n in available]
        dlg = QInputDialog(self.window())
        dlg.setWindowTitle(tr("复制坐标"))
        dlg.setLabelText(tr("新坐标绑定哪个坐标点？（沿用源半径）："))
        dlg.setComboBoxItems(items)
        dlg.setStyleSheet(
            "QInputDialog { background-color: palette(window); }"
            "QLabel { color: palette(text); }"
            "QComboBox { background-color: palette(base); color: palette(text); padding: 4px; }"
            "QPushButton { padding: 4px 16px; }"
        )
        if not dlg.exec():
            return
        text = dlg.textValue()
        if not text:
            return
        key, _name = available[items.index(text)]
        offset = max(src.r_ratio * 2.0, 0.02)
        new_point = Point(
            key=key,
            cx_ratio=min(1.0, src.cx_ratio + offset),
            cy_ratio=min(1.0, src.cy_ratio + offset),
            r_ratio=src.r_ratio,
        )
        self._points.append(new_point)
        self._selected_point_idx = len(self._points) - 1
        self._selected_arrow_idx = -1
        self._notify_poi_changed()
        self.update()

    # ─── 几何辅助 ────────────────────────────────────────

    def _widget_to_canvas_norm(self, pos: QPointF) -> tuple[float, float]:
        """widget 坐标 -> 画布内归一化坐标"""
        sx, sy = self._widget_to_norm(pos)
        return self._screenshot_to_canvas_norm(sx, sy)

    def _point_handle_center(self, idx: int) -> QPointF:
        """point 半径手柄（圆环右侧中点）widget 坐标"""
        p = self._points[idx]
        c = self._point_center_widget(p)
        r = self._point_radius_pixels(p)
        return QPointF(c.x() + r, c.y())

    def _plus_button_center(self, idx: int) -> QPointF:
        """point 右上角 45° 的 + 按钮圆心 widget 坐标"""
        p = self._points[idx]
        c = self._point_center_widget(p)
        r = self._point_radius_pixels(p)
        d = r + PLUS_BTN_OFFSET + PLUS_BTN_RADIUS
        inv = 0.70710678  # cos/sin 45°
        return QPointF(c.x() + d * inv, c.y() - d * inv)

    @staticmethod
    def _dist_point_to_segment(p: QPointF, a: QPointF, b: QPointF) -> float:
        """点 p 到线段 ab 的最短距离（widget 像素）"""
        ax, ay, bx, by = a.x(), a.y(), b.x(), b.y()
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        if seg2 <= 1e-9:
            return math.hypot(p.x() - ax, p.y() - ay)
        t = ((p.x() - ax) * dx + (p.y() - ay) * dy) / seg2
        t = max(0.0, min(1.0, t))
        px, py = ax + t * dx, ay + t * dy
        return math.hypot(p.x() - px, p.y() - py)

    def _arrow_end_widget(self, a: Arrow) -> QPointF | None:
        """arrow 终点 widget 坐标（吸附态动态查 point，绝对态用固定坐标）"""
        if a.to_key is not None:
            tp = self._resolve_point(a.to_key)
            if tp is None:
                return None
            return self._point_center_widget(tp)
        if a.to_cx_ratio is None or a.to_cy_ratio is None:
            return None
        return self._canvas_norm_center_widget(a.to_cx_ratio, a.to_cy_ratio)

    # ─── 命中检测 ────────────────────────────────────────

    def _hit_point(self, pos: QPointF) -> int:
        """命中哪个 point（返回索引，-1 无）；从后往前，上层优先"""
        for i in range(len(self._points) - 1, -1, -1):
            c = self._point_center_widget(self._points[i])
            r = max(self._point_radius_pixels(self._points[i]), POINT_HANDLE_SIZE)
            if math.hypot(pos.x() - c.x(), pos.y() - c.y()) <= r:
                return i
        return -1

    def _hit_point_handle(self, pos: QPointF, idx: int) -> bool:
        c = self._point_handle_center(idx)
        return math.hypot(pos.x() - c.x(), pos.y() - c.y()) <= POINT_HANDLE_SIZE + 2

    def _hit_plus_button(self, pos: QPointF, idx: int) -> bool:
        c = self._plus_button_center(idx)
        return math.hypot(pos.x() - c.x(), pos.y() - c.y()) <= PLUS_BTN_RADIUS

    def _hit_arrow(self, pos: QPointF) -> int:
        """命中哪个 arrow（返回索引，-1 无）"""
        for i in range(len(self._arrows) - 1, -1, -1):
            a = self._arrows[i]
            fp = self._resolve_point(a.from_key)
            end = self._arrow_end_widget(a)
            if fp is None or end is None:
                continue
            start = self._point_center_widget(fp)
            if self._dist_point_to_segment(pos, start, end) <= 6:
                return i
        return -1

    def _nearest_point_for_snap(self, pos: QPointF, exclude_key: str) -> int:
        """找距 pos 最近且在吸附阈值内的 point（排除 exclude_key），-1 无"""
        best_idx = -1
        best_d = float(ARROW_SNAP_PIXELS)
        for i, p in enumerate(self._points):
            if p.key == exclude_key:
                continue
            c = self._point_center_widget(p)
            d = math.hypot(pos.x() - c.x(), pos.y() - c.y())
            if d <= best_d:
                best_d = d
                best_idx = i
        return best_idx

    # ─── 鼠标事件介入（由 canvas_interaction 早期调用） ──

    def _poi_handle_press(self, event) -> bool:
        """返回 True 表示已消费该按下事件（region 逻辑不再处理）"""
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        pos = event.position()

        # 落点模式：单击放置 point
        if self._poi_action == PoiAction.PLACE_POINT:
            cx, cy = self._widget_to_canvas_norm(pos)
            self._points.append(Point(
                key=self._pending_point_key,
                cx_ratio=cx, cy_ratio=cy, r_ratio=self._pending_point_r,
            ))
            self._selected_point_idx = len(self._points) - 1
            self._selected_arrow_idx = -1
            self.cancel_poi_action()
            self._notify_poi_changed()
            self.update()
            return True

        # 画箭头模式：单击设置终点
        if self._poi_action == PoiAction.DRAW_ARROW:
            self._finalize_arrow(pos)
            return True

        # 默认模式：命中 point 的 + 按钮 / 半径手柄 / 圆体，或命中 arrow
        if self._selected_point_idx >= 0:
            selected = self._points[self._selected_point_idx]
            if not selected.is_reference:
                if self._hit_plus_button(pos, self._selected_point_idx):
                    self.begin_draw_arrow(selected.key)
                    return True
                if self._hit_point_handle(pos, self._selected_point_idx):
                    self._poi_drag = PoiDrag.RESIZE_POINT
                    self.update()
                    return True

        pidx = self._hit_point(pos)
        if pidx >= 0:
            self._selected_point_idx = pidx
            self._selected_arrow_idx = -1
            self._poi_drag = (PoiDrag.NONE if self._points[pidx].is_reference
                              else PoiDrag.MOVE_POINT)
            self._poi_drag_moved = False
            # 仅选中，数据未变 → 不能标记 dirty
            self._notify_selection_changed()
            self.update()
            return True

        aidx = self._hit_arrow(pos)
        if aidx >= 0:
            self._selected_arrow_idx = aidx
            self._selected_point_idx = -1
            a = self._arrows[aidx]
            if a.to_key is None:
                self._poi_drag = PoiDrag.MOVE_ARROW_END
                self._poi_drag_moved = False
            self._notify_selection_changed()
            self.update()
            return True

        # 未命中任何 POI：清除 POI 选中，交还给 region 逻辑
        if self._selected_point_idx >= 0 or self._selected_arrow_idx >= 0:
            self._selected_point_idx = -1
            self._selected_arrow_idx = -1
            self.update()
        return False

    def _collect_point_snap_targets(self, exclude_idx: int) -> tuple[list[float], list[float]]:
        """收集其他点的吸附参考线（归一化）
        返回 (xs, ys)：xs 为竖线 x 值，ys 为横线 y 值（仅中心点）
        """
        xs: list[float] = []
        ys: list[float] = []
        for i, p in enumerate(self._points):
            if i == exclude_idx:
                continue
            xs.append(p.cx_ratio)
            ys.append(p.cy_ratio)
        return xs, ys

    def _apply_point_move_snap(self, p) -> None:
        """移动点时向其他点的中心吸附对齐"""
        xs, ys = self._collect_point_snap_targets(self._selected_point_idx)
        thx = self._snap_threshold_x()
        thy = self._snap_threshold_y()
        self._snap_lines_x = []
        self._snap_lines_y = []

        # x 方向：找最近的吸附目标
        best_x = self._nearest(p.cx_ratio, xs, thx)
        if best_x is not None:
            p.cx_ratio = max(0.0, min(1.0, best_x))
            self._snap_lines_x.append(best_x)

        # y 方向
        best_y = self._nearest(p.cy_ratio, ys, thy)
        if best_y is not None:
            p.cy_ratio = max(0.0, min(1.0, best_y))
            self._snap_lines_y.append(best_y)

    def _poi_handle_move(self, event) -> bool:
        pos = event.position()

        # 死区内不改数据，但事件仍算被 POI 消费掉，避免落到 region 分支去
        # 移动区域（理由见 canvas_interaction.DRAG_DEAD_ZONE_PX）
        if self._poi_drag != PoiDrag.NONE and not self._beyond_dead_zone(pos):
            return True

        if self._poi_drag == PoiDrag.MOVE_POINT and self._selected_point_idx >= 0:
            cx, cy = self._widget_to_canvas_norm(pos)
            p = self._points[self._selected_point_idx]
            p.cx_ratio, p.cy_ratio = cx, cy
            # Shift 按下时禁用吸附
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self._snap_lines_x = []
                self._snap_lines_y = []
            else:
                self._apply_point_move_snap(p)
            self._poi_drag_moved = True
            self.update()
            return True

        if self._poi_drag == PoiDrag.RESIZE_POINT and self._selected_point_idx >= 0:
            p = self._points[self._selected_point_idx]
            c = self._point_center_widget(p)
            dist = math.hypot(pos.x() - c.x(), pos.y() - c.y())
            rect = self._canvas_rect_widget()
            base = min(rect.width(), rect.height())
            if base > 0:
                p.r_ratio = max(0.003, min(0.2, dist / base))
                self._poi_drag_moved = True
            self.update()
            return True

        if self._poi_drag == PoiDrag.MOVE_ARROW_END and self._selected_arrow_idx >= 0:
            cx, cy = self._widget_to_canvas_norm(pos)
            a = self._arrows[self._selected_arrow_idx]
            a.to_cx_ratio, a.to_cy_ratio = cx, cy
            self._poi_drag_moved = True
            self.update()
            return True

        if self._poi_action == PoiAction.DRAW_ARROW:
            self._poi_cursor = pos
            self._arrow_last_move_ms = time.monotonic() * 1000
            self._arrow_snap_idx = -1  # 移动过程中取消吸附提示（停手才吸附）
            self.update()
            return True

        if self._poi_action == PoiAction.PLACE_POINT:
            self._poi_cursor = pos
            self.update()
            return True

        return False

    def _poi_handle_release(self, event) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        if self._poi_drag != PoiDrag.NONE:
            moved = self._poi_drag_moved
            self._poi_drag = PoiDrag.NONE
            self._poi_drag_orig = None
            self._poi_drag_moved = False
            # 清除吸附参考线
            self._snap_lines_x = []
            self._snap_lines_y = []
            # 纯点击（未拖动）不算数据变更
            if moved:
                self._notify_poi_changed()
            else:
                self._notify_selection_changed()
            self.update()
            return True
        return False

    def _poi_handle_escape(self) -> bool:
        """ESC 取消 point/arrow 创建动作"""
        if self._poi_action != PoiAction.NONE:
            self.cancel_poi_action()
            return True
        return False

    def _finalize_arrow(self, pos: QPointF):
        """结束画箭头：确定终点（吸附态或绝对态），弹框输入 key"""
        snap_idx = self._arrow_snap_idx
        if snap_idx < 0:
            # 松手瞬间再判定一次是否已停手吸附
            snap_idx = self._nearest_point_for_snap(pos, self._arrow_from_key)
        key = self._prompt_arrow_key()
        if not key:
            self.cancel_poi_action()
            return
        if snap_idx >= 0:
            arrow = Arrow(key=key, from_key=self._arrow_from_key,
                          to_key=self._points[snap_idx].key)
        else:
            cx, cy = self._widget_to_canvas_norm(pos)
            arrow = Arrow(key=key, from_key=self._arrow_from_key,
                          to_cx_ratio=cx, to_cy_ratio=cy)
        self._arrows.append(arrow)
        self._selected_arrow_idx = len(self._arrows) - 1
        self.cancel_poi_action()
        self._notify_poi_changed()
        self.update()

    def _prompt_arrow_key(self) -> str:
        """弹框输入 arrow key，做 id 命名校验 + arrows 空间唯一校验"""
        # 已存在集合必须含被视图过滤隐藏的实例，否则其他视图已绑定的方向 key 会被当成可用
        existing = {a.key for a in self._arrows + self._hidden_arrows}
        while True:
            dlg = QInputDialog(self.window())
            dlg.setWindowTitle(tr("命名方向"))
            dlg.setLabelText(tr("输入方向 key（小写字母开头，可含数字/下划线）："))
            dlg.setTextValue("")
            dlg.setStyleSheet(
                "QInputDialog { background-color: palette(window); }"
                "QLabel { color: palette(text); }"
                "QLineEdit { background-color: palette(base); color: palette(text); padding: 4px; }"
                "QPushButton { padding: 4px 16px; }"
            )
            ok = dlg.exec()
            if not ok:
                return ""
            key = dlg.textValue().strip()
            if not _RE_ARROW_KEY.match(key):
                QMessageBox.warning(self.window(), tr("命名非法"),
                                    tr("key 必须以小写字母开头，仅含小写字母/数字/下划线。"))
                continue
            if key in existing:
                QMessageBox.warning(self.window(), tr("key 重复"),
                                    f"方向 key「{key}」已存在，请换一个。")
                continue
            return key

    # ─── 停手吸附定时器回调（由 canvas.py QTimer 触发） ──

    def on_arrow_snap_tick(self):
        """周期性检查：画箭头时鼠标静止超过阈值则显示吸附提示"""
        if self._poi_action != PoiAction.DRAW_ARROW:
            return
        if self._poi_cursor.isNull():
            return
        now = time.monotonic() * 1000
        if now - self._arrow_last_move_ms < ARROW_SNAP_STILL_MS:
            return
        idx = self._nearest_point_for_snap(self._poi_cursor, self._arrow_from_key)
        if idx != self._arrow_snap_idx:
            self._arrow_snap_idx = idx
            self.update()

    # ─── 渲染 ────────────────────────────────────────────

    def _draw_points(self, painter: QPainter):
        """绘制所有 point + 落点预览"""
        for i, p in enumerate(self._points):
            c = self._point_center_widget(p)
            r = self._point_radius_pixels(p)
            selected = (i == self._selected_point_idx)

            painter.save()
            ring = QColor(POINT_COLOR)
            painter.setBrush(QBrush(POINT_FILL))
            painter.setPen(QPen(ring, 3 if selected else 2))
            painter.drawEllipse(c, r, r)
            # 中心点
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(ring))
            painter.drawEllipse(c, 2.5, 2.5)
            painter.restore()

            # 名称标签
            name = self._point_name(p.key)
            if name:
                painter.save()
                font = QFont("Microsoft YaHei", 9)
                painter.setFont(font)
                fm = painter.fontMetrics()
                tw = fm.horizontalAdvance(name) + 8
                th = fm.height() + 4
                label_rect = QRectF(c.x() - tw / 2, c.y() - r - th - 2, tw, th)
                painter.fillRect(label_rect, QColor(0, 0, 0, 180))
                painter.setPen(QColor(255, 255, 255))
                painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, name)
                painter.restore()

            if selected and not p.is_reference:
                # 半径手柄
                painter.save()
                painter.setPen(QPen(QColor(255, 255, 0), 1))
                painter.setBrush(QBrush(QColor(255, 255, 0)))
                h = self._point_handle_center(i)
                painter.drawRect(QRectF(h.x() - POINT_HANDLE_SIZE, h.y() - POINT_HANDLE_SIZE,
                                        POINT_HANDLE_SIZE * 2, POINT_HANDLE_SIZE * 2))
                painter.restore()
                # 右上角 + 按钮
                self._draw_plus_button(painter, self._plus_button_center(i))

        # 落点预览
        if self._poi_action == PoiAction.PLACE_POINT and not self._poi_cursor.isNull():
            rect = self._canvas_rect_widget()
            base = min(rect.width(), rect.height())
            r = self._pending_point_r * base
            painter.save()
            painter.setBrush(QBrush(POINT_FILL))
            painter.setPen(QPen(POINT_COLOR, 2, Qt.PenStyle.DashLine))
            painter.drawEllipse(self._poi_cursor, r, r)
            painter.restore()

    def _draw_plus_button(self, painter: QPainter, center: QPointF):
        painter.save()
        painter.setPen(QPen(QColor(0, 0, 0, 160), 1))
        painter.setBrush(QBrush(QColor(80, 200, 120)))
        painter.drawEllipse(center, PLUS_BTN_RADIUS, PLUS_BTN_RADIUS)
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        s = PLUS_BTN_RADIUS - 3
        painter.drawLine(QPointF(center.x() - s, center.y()), QPointF(center.x() + s, center.y()))
        painter.drawLine(QPointF(center.x(), center.y() - s), QPointF(center.x(), center.y() + s))
        painter.restore()

    def _draw_arrows(self, painter: QPainter):
        """绘制所有 arrow（彩色实线 + 实心箭头）+ 正在创建的箭头"""
        for i, a in enumerate(self._arrows):
            fp = self._resolve_point(a.from_key)
            end = self._arrow_end_widget(a)
            if fp is None or end is None:
                continue
            start = self._point_center_widget(fp)
            color = ARROW_COLORS[i % len(ARROW_COLORS)]
            selected = (i == self._selected_arrow_idx)
            self._draw_arrow_line(painter, start, end, color, 3 if selected else 2)
            # key 标签置于中点
            mid = QPointF((start.x() + end.x()) / 2, (start.y() + end.y()) / 2)
            painter.save()
            font = QFont("Microsoft YaHei", 8)
            painter.setFont(font)
            fm = painter.fontMetrics()
            tw = fm.horizontalAdvance(a.key) + 6
            th = fm.height() + 2
            label_rect = QRectF(mid.x() - tw / 2, mid.y() - th / 2, tw, th)
            painter.fillRect(label_rect, QColor(0, 0, 0, 170))
            painter.setPen(color.lighter(140))
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, a.key)
            painter.restore()
            # 绝对态终点显示可拖动小方块
            if selected and a.to_key is None:
                painter.save()
                painter.setPen(QPen(QColor(255, 255, 0), 1))
                painter.setBrush(QBrush(QColor(255, 255, 0)))
                painter.drawRect(QRectF(end.x() - POINT_HANDLE_SIZE, end.y() - POINT_HANDLE_SIZE,
                                        POINT_HANDLE_SIZE * 2, POINT_HANDLE_SIZE * 2))
                painter.restore()

        # 正在创建的箭头
        if self._poi_action == PoiAction.DRAW_ARROW and not self._poi_cursor.isNull():
            fp = self._resolve_point(self._arrow_from_key)
            if fp is not None:
                start = self._point_center_widget(fp)
                if self._arrow_snap_idx >= 0:
                    end = self._point_center_widget(self._points[self._arrow_snap_idx])
                    # 高亮吸附目标
                    painter.save()
                    tp = self._points[self._arrow_snap_idx]
                    tr = self._point_radius_pixels(tp)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.setPen(QPen(QColor(80, 200, 120), 3))
                    painter.drawEllipse(end, tr + 3, tr + 3)
                    painter.restore()
                else:
                    end = self._poi_cursor
                self._draw_arrow_line(painter, start, end, QColor(80, 200, 120), 2)

    def _draw_arrow_line(self, painter: QPainter, start: QPointF, end: QPointF,
                         color: QColor, width: int):
        """画一条带实心箭头的线段"""
        painter.save()
        painter.setPen(QPen(color, width))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(start, end)
        # 箭头
        angle = math.atan2(end.y() - start.y(), end.x() - start.x())
        size = 10 + width
        a1 = angle + math.radians(150)
        a2 = angle - math.radians(150)
        p1 = QPointF(end.x() + size * math.cos(a1), end.y() + size * math.sin(a1))
        p2 = QPointF(end.x() + size * math.cos(a2), end.y() + size * math.sin(a2))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(color))
        painter.drawPolygon(QPolygonF([end, p1, p2]))
        painter.restore()
