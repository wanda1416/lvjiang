"""画布组选区：Ctrl 多选与整组平移。

吸附只能把一个实体贴到别人的边或中心线上，表达不了「一组实体相对位置保持
精确不变地整体平移」。战斗、移动这类定义要求组内相对位置严格相等，一个个挪
必然在小数位上散掉，所以需要一个真正的组概念。

设计要点（完整语义见 docs/20-requirements/19-canvas-group-selection.md）：

- 选区按 ``(kind, key)`` 存，不存索引。表格那边已经为此踩过坑——视图过滤后
  row 不再对应场景定义的索引。选区指错实体的后果是把错的东西挪走。
- 参与的实体只有区域、坐标和网格。引用外框的内部实体跟随外框整体变换，
  ``to_key`` 型箭头跟两端的点走，它们进选区会被位移两次。
- 夹取按**组**做而不是逐个做。逐个夹时只要有一个成员撞到边界，组就被剪切
  变形，而这正是本功能要防的事。
- 整组移动期间**不吸附**：成员之间互相吸附会直接破坏组内相对位置，向外部
  实体吸附则让落点变得不可预期。Shift 在别处是「取消吸附」，这里要取消的
  东西本来就没开，因此是空操作——不反转成「按住才吸附」，同一个修饰键在
  不同上下文里含义相反最容易记错。
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen

from ...i18n import tr

#: 组成员高亮：描边色与填充色
GROUP_MEMBER_COLOR = QColor(80, 255, 170)
GROUP_MEMBER_FILL = QColor(80, 255, 170, 40)
#: 橡皮筋框
GROUP_BAND_COLOR = QColor(80, 255, 170)
GROUP_BAND_FILL = QColor(80, 255, 170, 25)

#: 参与组选区的实体类型。顺序即绘制与统计顺序。
GROUP_KINDS = ("region", "point", "panel")

_KIND_LABELS = {"region": "区域", "point": "坐标", "panel": "网格"}


class CanvasGroupMixin:
    """组选区与整组平移

    依赖主类提供:
        _regions, _points, _panels,
        _region_rect_widget(), _panel_rect_widget(),
        _point_center_widget(), _point_radius_pixels(),
        _widget_delta_to_canvas_norm(), _beyond_dead_zone(),
        _notify_changed(), _notify_poi_changed(), _notify_status(),
        _snap_lines_x, _snap_lines_y
    """

    _snap_lines_x: list[float]
    _snap_lines_y: list[float]

    def _init_group_state(self) -> None:
        #: 选区：{(kind, key)}，kind ∈ GROUP_KINDS
        self._group_selection: set[tuple[str, str]] = set()
        #: 橡皮筋框选的起点与当前点（widget 坐标）
        self._group_band_start: QPointF | None = None
        self._group_band_current: QPointF | None = None
        #: 整组拖动的按下位置，以及每个成员拖动前的坐标
        self._group_drag_start: QPointF | None = None
        self._group_drag_orig: dict[tuple[str, str], tuple[float, float]] = {}

    # ─── 成员查找 ────────────────────────────────────────

    def _group_items(self, kind: str) -> list:
        return {
            "region": self._regions,
            "point": self._points,
            "panel": self._panels,
        }[kind]

    def _group_member_objects(self) -> list[tuple[str, str, object]]:
        """当前选区对应的实体对象。

        选区按 key 存，所以这里每次按 key 现查——实体列表会因为刷新、视图
        过滤或删除而重建，缓存对象引用会指向已经不在画布上的旧实例。
        """
        found: list[tuple[str, str, object]] = []
        for kind in GROUP_KINDS:
            for item in self._group_items(kind):
                if (kind, item.key) in self._group_selection:
                    found.append((kind, item.key, item))
        return found

    def _group_entity_rect(self, kind: str, item) -> QRectF:
        """成员在 widget 中的外接矩形（坐标点按圆的外接正方形算）。"""
        if kind == "point":
            center = self._point_center_widget(item)
            r = self._point_radius_pixels(item)
            return QRectF(center.x() - r, center.y() - r, r * 2, r * 2)
        if kind == "panel":
            return self._panel_rect_widget(item)
        return self._region_rect_widget(item)

    def _group_hit(self, pos: QPointF) -> tuple[str, str] | None:
        """命中选区内的哪个成员（无则 None）。

        坐标点画在区域和网格之上，所以命中判定也按这个顺序倒着来，否则
        压在区域上的点永远点不到。
        """
        for kind in reversed(GROUP_KINDS):
            for item in self._group_items(kind):
                key = (kind, item.key)
                if key not in self._group_selection:
                    continue
                if self._group_entity_rect(kind, item).contains(pos):
                    return key
        return None

    def _group_hit_any(self, pos: QPointF) -> tuple[str, str] | None:
        """命中画布上的哪个可入组实体（不限于已选中的）。"""
        for kind in reversed(GROUP_KINDS):
            for item in self._group_items(kind):
                if self._group_entity_rect(kind, item).contains(pos):
                    return (kind, item.key)
        return None

    # ─── 选区增删 ────────────────────────────────────────

    def _group_toggle(self, member: tuple[str, str]) -> None:
        if member in self._group_selection:
            self._group_selection.discard(member)
        else:
            self._group_selection.add(member)
        self._group_report()

    def clear_group_selection(self) -> bool:
        """清空选区，返回是否真的清掉了东西（供调用方决定要不要重绘）。"""
        if not self._group_selection:
            return False
        self._group_selection.clear()
        self._group_band_start = None
        self._group_band_current = None
        self._group_drag_start = None
        self._group_drag_orig = {}
        self._group_report()
        return True

    def _group_report(self) -> None:
        """选区是个画布上的状态，列表里没有对应表现，必须在状态栏说出来。"""
        if not self._group_selection:
            self._notify_status(tr("已取消多选"))
            return
        counts = {kind: 0 for kind in GROUP_KINDS}
        for kind, _key, _item in self._group_member_objects():
            counts[kind] += 1
        detail = " / ".join(
            f"{tr(_KIND_LABELS[kind])} {counts[kind]}"
            for kind in GROUP_KINDS if counts[kind]
        )
        self._notify_status(
            tr("已选中 {total} 项（{detail}）；拖动任一项整组平移，"
               "点击空白处取消").format(
                total=sum(counts.values()), detail=detail))

    # ─── 橡皮筋框选 ──────────────────────────────────────

    def _group_band_begin(self, pos: QPointF) -> None:
        self._group_band_start = pos
        self._group_band_current = pos

    def _group_band_update(self, pos: QPointF) -> None:
        self._group_band_current = pos

    def _group_band_finish(self) -> None:
        """释放橡皮筋：**完全落入**框内的实体加入选区。

        用「完全落入」而不是「相交」：框选一组紧挨着的实体时，相交判定会把
        边上擦到的邻居一起拉进来，而组移动挪错一个实体很难当场看出来。
        """
        if self._group_band_start is None or self._group_band_current is None:
            return
        band = QRectF(
            self._group_band_start, self._group_band_current).normalized()
        self._group_band_start = None
        self._group_band_current = None
        if band.width() < 1 or band.height() < 1:
            return
        for kind in GROUP_KINDS:
            for item in self._group_items(kind):
                if band.contains(self._group_entity_rect(kind, item)):
                    self._group_selection.add((kind, item.key))
        self._group_report()

    # ─── 整组平移 ────────────────────────────────────────

    def _group_drag_begin(self, pos: QPointF) -> None:
        """记录每个成员的原始坐标，后续按「原始 + 完整位移」一次算出结果。"""
        self._group_drag_start = pos
        self._group_drag_orig = {}
        for kind, key, item in self._group_member_objects():
            if kind == "point":
                self._group_drag_orig[(kind, key)] = (
                    item.cx_ratio, item.cy_ratio)
            else:
                self._group_drag_orig[(kind, key)] = (
                    item.x_ratio, item.y_ratio)

    def _group_drag_bounds(self) -> tuple[float, float, float, float]:
        """组内允许的位移区间 (min_dx, max_dx, min_dy, max_dy)。

        逐个成员夹 0..1 会让先撞边界的那个停下、其余继续走，组被剪切变形。
        这里改成先求整组能走多远，再把同一个位移应用到所有成员。
        """
        min_dx = min_dy = -1.0
        max_dx = max_dy = 1.0
        for kind, key, item in self._group_member_objects():
            orig = self._group_drag_orig.get((kind, key))
            if orig is None:
                continue
            ox, oy = orig
            if kind == "point":
                # 圆心留出半径，否则圆会被切掉一半
                r = item.r_ratio
                lo_x, hi_x = r - ox, 1 - r - ox
                lo_y, hi_y = r - oy, 1 - r - oy
            else:
                lo_x, hi_x = -ox, 1 - item.w_ratio - ox
                lo_y, hi_y = -oy, 1 - item.h_ratio - oy
            min_dx, max_dx = max(min_dx, lo_x), min(max_dx, hi_x)
            min_dy, max_dy = max(min_dy, lo_y), min(max_dy, hi_y)
        # 组比画布还大时区间会反过来，此时只能原地不动
        if min_dx > max_dx:
            min_dx = max_dx = 0.0
        if min_dy > max_dy:
            min_dy = max_dy = 0.0
        return min_dx, max_dx, min_dy, max_dy

    def _group_drag_update(self, pos: QPointF) -> None:
        if self._group_drag_start is None or not self._group_drag_orig:
            return
        if not self._beyond_dead_zone(pos):
            return  # 死区内：点击时的手抖不得挪动整组
        dx, dy = self._widget_delta_to_canvas_norm(self._group_drag_start, pos)
        min_dx, max_dx, min_dy, max_dy = self._group_drag_bounds()
        dx = max(min_dx, min(max_dx, dx))
        dy = max(min_dy, min(max_dy, dy))
        for kind, key, item in self._group_member_objects():
            orig = self._group_drag_orig.get((kind, key))
            if orig is None:
                continue
            ox, oy = orig
            if kind == "point":
                item.cx_ratio, item.cy_ratio = ox + dx, oy + dy
            else:
                item.x_ratio, item.y_ratio = ox + dx, oy + dy
            # 跨场景引用成员在本场景只保存位置覆盖，和单体移动一致
            if getattr(item, "is_reference", False):
                item.position_overridden = True
        # 整组移动不吸附，顺带确保上一次单体拖动留下的参考线不会残留
        self._snap_lines_x = []
        self._snap_lines_y = []

    def _group_drag_finish(self) -> bool:
        """结束整组拖动，返回坐标是否真的变了（纯点击选中不算数据变更）。"""
        if self._group_drag_start is None:
            return False
        changed = False
        for kind, key, item in self._group_member_objects():
            orig = self._group_drag_orig.get((kind, key))
            if orig is None:
                continue
            now = ((item.cx_ratio, item.cy_ratio) if kind == "point"
                   else (item.x_ratio, item.y_ratio))
            if now != orig:
                changed = True
        self._group_drag_start = None
        self._group_drag_orig = {}
        if changed:
            # 坐标点的改动还要刷新 POI 列表，区域/网格走通用脏标记
            if any(kind == "point"
                   for kind, _key, _item in self._group_member_objects()):
                self._notify_poi_changed()
            self._notify_changed()
        return changed

    # ─── 绘制 ────────────────────────────────────────────

    def _draw_group_selection(self, painter: QPainter) -> None:
        painter.save()
        painter.setPen(QPen(GROUP_MEMBER_COLOR, 2))
        painter.setBrush(GROUP_MEMBER_FILL)
        for kind, _key, item in self._group_member_objects():
            rect = self._group_entity_rect(kind, item)
            if rect.width() >= 1 and rect.height() >= 1:
                painter.drawRect(rect)
        if (self._group_band_start is not None
                and self._group_band_current is not None):
            band = QRectF(
                self._group_band_start, self._group_band_current).normalized()
            painter.setPen(QPen(GROUP_BAND_COLOR, 1, Qt.PenStyle.DashLine))
            painter.setBrush(GROUP_BAND_FILL)
            painter.drawRect(band)
        painter.restore()
