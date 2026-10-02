"""Ctrl 组选区在真实画布事件流上的接线。

单元用例守的是组逻辑本身，这里守的是「鼠标事件确实走到了那些分支」，以及一条
同等重要的反向契约：**不按 Ctrl 时，空白处拖拽新建区域的行为一个字节都不变**。
组选区是插在那条兜底分支前面的，接错位置会让最高频的操作失效。
"""
import numpy as np
import pytest
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent

from lvjiang.core.layout_models import Panel, Point, Region
from lvjiang.ui.scene_editor.canvas import RegionCanvas
from lvjiang.ui.scene_editor.canvas_poi import PoiDrag

pytestmark = pytest.mark.usefixtures("qapp")

_CTRL = Qt.KeyboardModifier.ControlModifier
_NONE = Qt.KeyboardModifier.NoModifier


def _canvas(qtbot) -> RegionCanvas:
    canvas = RegionCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(640, 360)
    # 没有底图时 _display_rect 是空矩形，所有实体的 widget 矩形退化成零尺寸，
    # 命中判定一律落空——这些用例必须有图才测得到真实的坐标换算。
    canvas.set_image(np.zeros((360, 640, 3), dtype=np.uint8))
    canvas.set_regions([
        Region("a", 0.1, 0.1, 0.1, 0.1),
        Region("b", 0.4, 0.1, 0.1, 0.1),
        Region("far", 0.8, 0.8, 0.1, 0.1),
    ])
    return canvas


def _center(canvas: RegionCanvas, key: str) -> QPointF:
    region = next(r for r in canvas.get_regions() if r.key == key)
    return canvas._region_rect_widget(region).center()


def _send(canvas, kind, pos, modifier=_NONE):
    event = QMouseEvent(
        kind, pos, QPointF(pos),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, modifier)
    {QEvent.Type.MouseButtonPress: canvas.mousePressEvent,
     QEvent.Type.MouseMove: canvas.mouseMoveEvent,
     QEvent.Type.MouseButtonRelease: canvas.mouseReleaseEvent}[kind](event)


def test_ctrl_click_builds_a_selection_and_plain_click_clears_it(qtbot):
    canvas = _canvas(qtbot)

    _send(canvas, QEvent.Type.MouseButtonPress, _center(canvas, "a"), _CTRL)
    _send(canvas, QEvent.Type.MouseButtonPress, _center(canvas, "b"), _CTRL)
    assert canvas._group_selection == {("region", "a"), ("region", "b")}

    # 点选区外的实体：选区作废，改为单选它
    _send(canvas, QEvent.Type.MouseButtonPress, _center(canvas, "far"), _NONE)
    assert canvas._group_selection == set()


def test_dragging_a_member_moves_the_whole_group_without_ctrl(qtbot):
    """松开 Ctrl 不丢选区——选好之后直接拖就行。"""
    canvas = _canvas(qtbot)
    for key in ("a", "b"):
        _send(canvas, QEvent.Type.MouseButtonPress, _center(canvas, key), _CTRL)

    start = _center(canvas, "a")
    moved = QPointF(start.x() + 40, start.y() + 20)
    _send(canvas, QEvent.Type.MouseButtonPress, start, _NONE)
    _send(canvas, QEvent.Type.MouseMove, moved, _NONE)
    _send(canvas, QEvent.Type.MouseButtonRelease, moved, _NONE)

    regions = {r.key: r for r in canvas.get_regions()}
    assert regions["a"].x_ratio > 0.1 and regions["b"].x_ratio > 0.4
    assert regions["b"].x_ratio - regions["a"].x_ratio == pytest.approx(0.3)
    assert regions["far"].x_ratio == pytest.approx(0.8), "未入组的实体不该动"
    assert canvas._group_selection == {("region", "a"), ("region", "b")}


def test_plain_drag_on_blank_still_creates_a_region(qtbot, monkeypatch):
    """反向契约：Ctrl 分支不能挡住「空白处拖拽新建区域」。"""
    canvas = _canvas(qtbot)
    monkeypatch.setattr(
        RegionCanvas, "_prompt_field_selection", lambda *_a: None)

    start = QPointF(200, 240)
    end = QPointF(300, 280)
    _send(canvas, QEvent.Type.MouseButtonPress, start, _NONE)
    _send(canvas, QEvent.Type.MouseMove, end, _NONE)
    _send(canvas, QEvent.Type.MouseButtonRelease, end, _NONE)

    # 字段绑定对话框被打桩，新区域此刻还没有 key，而 get_regions() 会把
    # 无 key 的过滤掉，所以这里看画布自己的列表。
    assert len(canvas._regions) == 4


def test_clicking_blank_cancels_the_selection_without_leaving_a_region(
    qtbot, monkeypatch,
):
    """取消多选的那一下点击不能顺手留下一个垃圾区域。"""
    canvas = _canvas(qtbot)
    monkeypatch.setattr(
        RegionCanvas, "_prompt_field_selection", lambda *_a: None)
    _send(canvas, QEvent.Type.MouseButtonPress, _center(canvas, "a"), _CTRL)

    blank = QPointF(200, 240)
    _send(canvas, QEvent.Type.MouseButtonPress, blank, _NONE)
    _send(canvas, QEvent.Type.MouseButtonRelease, blank, _NONE)

    assert canvas._group_selection == set()
    assert len(canvas._regions) == 3


def test_dragging_a_selected_point_moves_the_whole_group(qtbot):
    """点一个已入组的坐标点必须整组走。

    坐标点、网格、引用各有自己的命中分支，一命中就 return 并启动单体拖动。
    组分支排在它们后面时，这一下只会挪走被点中的那个点——这正是接入点
    必须在那三条分支之前的原因。
    """
    canvas = _canvas(qtbot)
    canvas.set_points([
        Point("p1", 0.2, 0.5, 0.03),
        Point("p2", 0.6, 0.5, 0.03),
    ])
    for key in ("p1", "p2"):
        _send(canvas, QEvent.Type.MouseButtonPress,
              canvas._point_center_widget(
                  next(p for p in canvas.get_points() if p.key == key)),
              _CTRL)
    assert canvas._group_selection == {("point", "p1"), ("point", "p2")}

    start = canvas._point_center_widget(
        next(p for p in canvas.get_points() if p.key == "p1"))
    moved = QPointF(start.x() + 40, start.y())
    _send(canvas, QEvent.Type.MouseButtonPress, start, _NONE)
    # 接入点正确时 POI 的命中分支根本不该被走到；它一旦接手就会置
    # _poi_drag = MOVE_POINT，然后只挪这一个点
    assert canvas._poi_drag == PoiDrag.NONE
    assert canvas._group_drag_start is not None
    _send(canvas, QEvent.Type.MouseMove, moved, _NONE)
    _send(canvas, QEvent.Type.MouseButtonRelease, moved, _NONE)

    points = {p.key: p for p in canvas.get_points()}
    assert points["p1"].cx_ratio > 0.2, "被点中的点要动"
    assert points["p2"].cx_ratio > 0.6, "同组的另一个点也要动"
    assert points["p2"].cx_ratio - points["p1"].cx_ratio == pytest.approx(0.4)


def test_dragging_a_selected_panel_moves_the_whole_group(qtbot):
    """网格的命中分支同样排在组分支之后，一起守住。"""
    canvas = _canvas(qtbot)
    canvas.set_panels([
        Panel("g1", 0.1, 0.6, 0.1, 0.1, rows=2, cols=2),
        Panel("g2", 0.4, 0.6, 0.1, 0.1, rows=2, cols=2),
    ])
    for key in ("g1", "g2"):
        panel = next(p for p in canvas.get_panels() if p.key == key)
        _send(canvas, QEvent.Type.MouseButtonPress,
              canvas._panel_rect_widget(panel).center(), _CTRL)
    assert canvas._group_selection == {("panel", "g1"), ("panel", "g2")}

    panel = next(p for p in canvas.get_panels() if p.key == "g1")
    start = canvas._panel_rect_widget(panel).center()
    moved = QPointF(start.x(), start.y() - 40)
    _send(canvas, QEvent.Type.MouseButtonPress, start, _NONE)
    _send(canvas, QEvent.Type.MouseMove, moved, _NONE)
    _send(canvas, QEvent.Type.MouseButtonRelease, moved, _NONE)

    panels = {p.key: p for p in canvas.get_panels()}
    assert panels["g1"].y_ratio < 0.6 and panels["g2"].y_ratio < 0.6
    assert panels["g2"].x_ratio - panels["g1"].x_ratio == pytest.approx(0.3)
