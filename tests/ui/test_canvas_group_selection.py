"""画布组选区与整组平移。

这个功能的全部价值在一条契约上：**整组平移后，成员之间的相对位移完全相等**。
战斗、移动这类定义靠它保住严格相等的相对位置，而散掉之后没有任何地方会报错
——区域之间的关系没有校验。所以这里的用例都围着那条契约和会破坏它的三种方式：
逐个夹边界、成员互相吸附、选区指错实体。
"""
import pytest
from PyQt6.QtCore import QPointF, QRectF

from lvjiang.core.layout_models import Arrow, Point, Region
from lvjiang.ui.scene_editor.canvas_group import (
    GROUP_KINDS,
    CanvasGroupMixin,
)


class _Host(CanvasGroupMixin):
    """最小宿主：几何换算用 1:1 的假画布，只验组逻辑本身。

    1 归一化单位 = 100 widget 像素，于是用例里的位移可以直接按像素写。
    """

    SCALE = 100.0

    def __init__(self, regions=(), points=(), panels=(), arrows=()):
        self._regions = list(regions)
        self._points = list(points)
        self._panels = list(panels)
        self._arrows = list(arrows)
        self._snap_lines_x: list[float] = []
        self._snap_lines_y: list[float] = []
        self._press_pos = QPointF(0, 0)
        self.changed = 0
        self.poi_changed = 0
        self.status: list[str] = []
        self._init_group_state()

    # ── 几何 ──
    def _region_rect_widget(self, r):
        return QRectF(r.x_ratio * self.SCALE, r.y_ratio * self.SCALE,
                      r.w_ratio * self.SCALE, r.h_ratio * self.SCALE)

    _panel_rect_widget = _region_rect_widget

    def _point_center_widget(self, p):
        return QPointF(p.cx_ratio * self.SCALE, p.cy_ratio * self.SCALE)

    def _point_radius_pixels(self, p):
        return p.r_ratio * self.SCALE

    def _widget_delta_to_canvas_norm(self, start, end):
        return ((end.x() - start.x()) / self.SCALE,
                (end.y() - start.y()) / self.SCALE)

    def _beyond_dead_zone(self, _pos):
        return True

    # ── 通知 ──
    def _notify_changed(self):
        self.changed += 1

    def _notify_poi_changed(self):
        self.poi_changed += 1

    def _notify_status(self, message):
        self.status.append(message)

    # ── 用例便捷方法 ──
    def drag(self, dx, dy, *, frm=None):
        frm = QPointF(0, 0) if frm is None else frm
        self._group_drag_begin(frm)
        self._group_drag_update(QPointF(frm.x() + dx, frm.y() + dy))
        return self._group_drag_finish()


def _region(key, x, y, w=0.1, h=0.1):
    return Region(key=key, x_ratio=x, y_ratio=y, w_ratio=w, h_ratio=h)


def _point(key, cx, cy, r=0.02):
    return Point(key=key, cx_ratio=cx, cy_ratio=cy, r_ratio=r)


def _offsets(items):
    """相对第一个成员的位移，组移动前后必须完全一致。"""
    first = items[0]
    return [(round(i.x_ratio - first.x_ratio, 12),
             round(i.y_ratio - first.y_ratio, 12)) for i in items]


# ─── 核心契约 ────────────────────────────────────────────


def test_group_move_keeps_relative_offsets_exactly():
    """这是整个功能的理由：组内相对位移在平移后一字不差。"""
    host = _Host(regions=[_region("a", 0.1, 0.1),
                          _region("b", 0.3, 0.17),
                          _region("c", 0.25, 0.4)])
    host._group_selection = {("region", k) for k in "abc"}
    before = _offsets(host._regions)

    assert host.drag(7, 11) is True

    assert _offsets(host._regions) == before
    assert host._regions[0].x_ratio == pytest.approx(0.17)


def test_group_stops_together_at_the_canvas_edge():
    """逐个夹边界会把队形剪变形——先撞边的停下、其余继续走。

    这里 b 的右边缘距画布右侧只剩 0.05，所以整组最多只能右移 0.05，
    a 不该因为自己还有空间就多走。
    """
    host = _Host(regions=[_region("a", 0.1, 0.1),
                          _region("b", 0.85, 0.1)])
    host._group_selection = {("region", "a"), ("region", "b")}
    before = _offsets(host._regions)

    host.drag(40, 0)   # 想右移 0.4，实际只能走 0.05

    assert _offsets(host._regions) == before
    assert host._regions[1].x_ratio == pytest.approx(0.9)
    assert host._regions[0].x_ratio == pytest.approx(0.15)


def test_group_move_never_leaves_snap_guides():
    """整组移动不吸附，也不能留下上一次单体拖动的参考线。"""
    host = _Host(regions=[_region("a", 0.1, 0.1), _region("b", 0.3, 0.1)])
    host._group_selection = {("region", "a"), ("region", "b")}
    host._snap_lines_x = [0.5]
    host._snap_lines_y = [0.5]

    host.drag(5, 5)

    assert host._snap_lines_x == []
    assert host._snap_lines_y == []


def test_point_members_keep_their_radius_inside_the_canvas():
    """坐标点的边界要留出半径，否则圆被切掉一半。"""
    host = _Host(points=[_point("p", 0.5, 0.5, r=0.02)])
    host._group_selection = {("point", "p")}

    host.drag(100, 0)

    assert host._points[0].cx_ratio == pytest.approx(0.98)


def test_mixed_kinds_move_by_the_same_delta():
    """区域、坐标、网格混选时位移必须一致，否则跨类型的相对关系就散了。"""
    host = _Host(regions=[_region("r", 0.2, 0.2)],
                 points=[_point("p", 0.25, 0.25)])
    host._group_selection = {("region", "r"), ("point", "p")}

    host.drag(10, -5)

    assert host._regions[0].x_ratio == pytest.approx(0.3)
    assert host._points[0].cx_ratio == pytest.approx(0.35)
    assert host._regions[0].y_ratio == pytest.approx(0.15)
    assert host._points[0].cy_ratio == pytest.approx(0.2)


# ─── 选区语义 ────────────────────────────────────────────


def test_selection_is_resolved_by_key_not_index():
    """按 key 现查：列表重建后选区仍指向同一个实体。

    存索引的话，一次视图过滤或删除就会让选区指向邻居，而组移动挪错实体
    不会报错，也很难当场发现。
    """
    host = _Host(regions=[_region("a", 0.1, 0.1), _region("b", 0.5, 0.5)])
    host._group_selection = {("region", "b")}

    # 模拟刷新：顺序变了，a 被过滤掉
    host._regions = [_region("b", 0.5, 0.5)]
    host.drag(10, 0)

    assert host._regions[0].x_ratio == pytest.approx(0.6)


def test_selection_ignores_keys_that_disappeared():
    """删掉的成员不该让整组移动崩掉，剩下的照常走。"""
    host = _Host(regions=[_region("a", 0.1, 0.1)])
    host._group_selection = {("region", "a"), ("region", "gone")}

    assert host.drag(10, 0) is True
    assert host._regions[0].x_ratio == pytest.approx(0.2)


def test_ctrl_click_toggles_membership():
    host = _Host(regions=[_region("a", 0.1, 0.1)])

    host._group_toggle(("region", "a"))
    assert host._group_selection == {("region", "a")}

    host._group_toggle(("region", "a"))
    assert host._group_selection == set()


def test_band_only_takes_entities_fully_inside():
    """相交判定会把边上擦到的邻居一起拉进来。"""
    host = _Host(regions=[_region("inside", 0.2, 0.2),
                          _region("straddling", 0.45, 0.2)])
    host._group_band_begin(QPointF(10, 10))
    host._group_band_update(QPointF(50, 50))

    host._group_band_finish()

    assert host._group_selection == {("region", "inside")}


def test_clearing_reports_and_resets_every_substate():
    host = _Host(regions=[_region("a", 0.1, 0.1)])
    host._group_selection = {("region", "a")}
    host._group_band_start = QPointF(0, 0)
    host._group_drag_start = QPointF(0, 0)

    assert host.clear_group_selection() is True

    assert host._group_selection == set()
    assert host._group_band_start is None
    assert host._group_drag_start is None
    assert host.clear_group_selection() is False, "空选区不该再报一次"


def test_status_line_names_the_selection():
    """选区在右侧列表里没有对应表现，状态栏是唯一的可见性。"""
    host = _Host(regions=[_region("a", 0.1, 0.1)],
                 points=[_point("p", 0.5, 0.5)])
    host._group_selection = {("region", "a"), ("point", "p")}

    host._group_report()

    assert "2" in host.status[-1]
    assert "区域" in host.status[-1] and "坐标" in host.status[-1]


# ─── 脏标记 ──────────────────────────────────────────────


def test_click_without_movement_is_not_a_data_change():
    """纯点击选中不能把布局标脏，否则「放弃改动」会被噪声填满。"""
    host = _Host(regions=[_region("a", 0.1, 0.1)])
    host._group_selection = {("region", "a")}

    assert host.drag(0, 0) is False
    assert host.changed == 0


def test_moving_points_also_refreshes_the_poi_lists():
    host = _Host(points=[_point("p", 0.5, 0.5)])
    host._group_selection = {("point", "p")}

    host.drag(5, 0)

    assert host.poi_changed == 1
    assert host.changed == 1


def test_moving_only_rects_does_not_touch_the_poi_lists():
    host = _Host(regions=[_region("a", 0.1, 0.1)])
    host._group_selection = {("region", "a")}

    host.drag(5, 0)

    assert host.poi_changed == 0
    assert host.changed == 1


def test_reference_members_record_a_position_override():
    """跨场景引用在本场景只存位置覆盖，和单体移动保持一致。"""
    region = _region("ref", 0.2, 0.2)
    region.source_scene = "other"
    host = _Host(regions=[region])
    host._group_selection = {("region", "ref")}

    host.drag(5, 0)

    assert region.position_overridden is True


# ─── 范围 ────────────────────────────────────────────────


def test_only_regions_points_and_panels_participate():
    """引用外框和方向不进组：前者内部实体跟外框走，后者跟两端的点走，
    进组都会被位移两次。"""
    assert GROUP_KINDS == ("region", "point", "panel")


# ─── 方向跟随 ────────────────────────────────────────────


def test_absolute_arrow_end_follows_its_point():
    """定义在组内坐标点上的方向必须跟着走，否则整组挪完方向就全变了。

    绝对态终点不绑任何实体，起点的点挪走之后方向向量就变了。
    """
    host = _Host(
        points=[_point("p", 0.2, 0.2)],
        arrows=[Arrow(key="a", from_key="p",
                      to_cx_ratio=0.4, to_cy_ratio=0.3)])
    host._group_selection = {("point", "p")}

    host.drag(10, 5)

    arrow = host._arrows[0]
    point = host._points[0]
    # 方向向量不变才叫「跟着走」
    assert arrow.to_cx_ratio - point.cx_ratio == pytest.approx(0.2)
    assert arrow.to_cy_ratio - point.cy_ratio == pytest.approx(0.1)


def test_snapped_arrow_is_left_alone():
    """吸附态终点绑在另一个点上：那个点在组里就自动跟随，不在组里就该被
    拉长——两种情况都不该在这里动它。"""
    host = _Host(
        points=[_point("p", 0.2, 0.2), _point("q", 0.6, 0.6)],
        arrows=[Arrow(key="a", from_key="p", to_key="q")])
    host._group_selection = {("point", "p")}

    host.drag(10, 0)

    arrow = host._arrows[0]
    assert arrow.to_key == "q"
    assert arrow.to_cx_ratio is None and arrow.to_cy_ratio is None


def test_arrow_whose_start_is_not_selected_does_not_move():
    host = _Host(
        points=[_point("p", 0.2, 0.2), _point("other", 0.7, 0.7)],
        arrows=[Arrow(key="a", from_key="other",
                      to_cx_ratio=0.9, to_cy_ratio=0.9)])
    host._group_selection = {("point", "p")}

    host.drag(10, 0)

    assert host._arrows[0].to_cx_ratio == pytest.approx(0.9)


def test_following_arrow_end_takes_part_in_the_group_bounds():
    """终点贴边时整组一起停：否则终点被单独夹在 0..1 上，方向当场改变。"""
    host = _Host(
        points=[_point("p", 0.2, 0.2)],
        arrows=[Arrow(key="a", from_key="p",
                      to_cx_ratio=0.95, to_cy_ratio=0.2)])
    host._group_selection = {("point", "p")}

    host.drag(100, 0)   # 想右移 1.0，终点只剩 0.05 的余量

    arrow = host._arrows[0]
    point = host._points[0]
    assert arrow.to_cx_ratio == pytest.approx(1.0)
    assert point.cx_ratio == pytest.approx(0.25)
    assert arrow.to_cx_ratio - point.cx_ratio == pytest.approx(0.75)


def test_moving_a_following_arrow_marks_the_layout_dirty():
    host = _Host(
        points=[_point("p", 0.2, 0.2)],
        arrows=[Arrow(key="a", from_key="p",
                      to_cx_ratio=0.4, to_cy_ratio=0.2)])
    host._group_selection = {("point", "p")}

    assert host.drag(5, 0) is True
    assert host.poi_changed == 1
