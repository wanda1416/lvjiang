"""画布拖拽死区：点击时的手抖不算修改。

场景编辑器里"只是点一下某个区域"却被标记成场景已修改。根因不是 dirty
判定写错了——按下/松开两端确实对比了几何：真·零位移的点击本来就判为
未修改。问题出在真实鼠标身上：点击瞬间常有 1-2 px 抖动，触摸板更明显，
于是一次"点击"变成了一次位移 0.001 量级归一化距离的**真拖动**。屏幕上
看不出任何变化，用户也没打算改，数据却真的变了。

后果不只是多一个绿点提示：保存时该场景会被整份写进 config/local 影子，
而实体文件的 local 影子是整文件遮盖，此后这个场景再也收不到系统更新与
在线下发。config/local 又在 .gitignore 里，用户执行 git diff 什么也看不到，
只会觉得"提示我改了，但根本没有 diff"——正是这个 bug 被发现时的描述。

因此在 widget 像素层面设死区：位移未超过 DRAG_DEAD_ZONE_PX 一律不改数据。
"""
from __future__ import annotations

import numpy as np
import pytest
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from lvjiang.core.layout_models import Panel, Point, Region
from lvjiang.ui.scene_editor.canvas import RegionCanvas
from lvjiang.ui.scene_editor.canvas_interaction import DRAG_DEAD_ZONE_PX


@pytest.fixture
def canvas(qtbot):
    c = RegionCanvas()
    qtbot.addWidget(c)
    c.resize(800, 600)
    c.set_image(np.zeros((600, 800, 3), dtype=np.uint8))
    c.set_regions([Region(key="foo", x_ratio=0.2, y_ratio=0.2,
                          w_ratio=0.3, h_ratio=0.3)])
    c.set_points([Point(key="p1", cx_ratio=0.7, cy_ratio=0.7, r_ratio=0.05)])
    c.set_panels([Panel(key="pan1", x_ratio=0.05, y_ratio=0.75,
                        w_ratio=0.2, h_ratio=0.15, cols=2, rows=2)])
    c._dirty_hits = 0
    for name in ("on_region_changed", "on_poi_changed", "on_panel_changed"):
        setattr(c, name, lambda c=c: setattr(c, "_dirty_hits", c._dirty_hits + 1))
    return c


def _snapshot(c) -> tuple:
    return ([r.to_dict() for r in c.get_regions()],
            [p.to_dict() for p in c.get_points()],
            [p.to_dict() for p in c.get_panels()])


def _drag(c, start: QPointF, dx: float, dy: float, steps: int = 1) -> None:
    """按下 → 移动 → 松开，走真实 Qt 事件派发（直接调 mousePressEvent 会
    落到 QWidget 的空实现上，因为 RegionCanvas 的 MRO 里 QWidget 在混入之前）。"""
    def send(kind, p, btn, btns):
        QApplication.sendEvent(c, QMouseEvent(
            kind, p, p, btn, btns, Qt.KeyboardModifier.NoModifier))

    send(QEvent.Type.MouseButtonPress, start,
         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    for i in range(1, steps + 1):
        send(QEvent.Type.MouseMove,
             QPointF(start.x() + dx * i / steps, start.y() + dy * i / steps),
             Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
    end = QPointF(start.x() + dx, start.y() + dy)
    send(QEvent.Type.MouseButtonRelease, end,
         Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)


def _region_center(c) -> QPointF:
    return c._region_rect_widget(c.get_regions()[0]).center()


class TestDeadZoneSuppressesJitter:
    @pytest.mark.parametrize("dx,dy", [(0, 0), (1, 0), (0, 1), (2, 1), (3, 0)])
    def test_jitter_does_not_change_region(self, canvas, dx, dy):
        before = _snapshot(canvas)
        _drag(canvas, _region_center(canvas), dx, dy)
        assert _snapshot(canvas) == before, "死区内的位移不该改动数据"
        assert canvas._dirty_hits == 0, "死区内的位移不该标记场景已修改"

    def test_jitter_does_not_change_point(self, canvas):
        before = _snapshot(canvas)
        _drag(canvas, canvas._canvas_norm_center_widget(0.7, 0.7), 2, 1)
        assert _snapshot(canvas) == before
        assert canvas._dirty_hits == 0

    def test_jitter_does_not_change_panel(self, canvas):
        before = _snapshot(canvas)
        _drag(canvas, canvas._panel_rect_widget(canvas.get_panels()[0]).center(), 2, 1)
        assert _snapshot(canvas) == before
        assert canvas._dirty_hits == 0


class TestRealDragStillWorks:
    """死区只挡手抖，不能把正常拖拽也一起挡掉。"""

    def test_drag_beyond_dead_zone_moves_region(self, canvas):
        before = _snapshot(canvas)
        _drag(canvas, _region_center(canvas), 20, 10)
        assert _snapshot(canvas) != before
        assert canvas._dirty_hits >= 1

    def test_exactly_at_threshold_counts_as_drag(self, canvas):
        before = _snapshot(canvas)
        _drag(canvas, _region_center(canvas), DRAG_DEAD_ZONE_PX, 0)
        assert _snapshot(canvas) != before, "达到阈值即应视为拖拽"

    def test_drag_beyond_dead_zone_moves_point(self, canvas):
        before = _snapshot(canvas)
        _drag(canvas, canvas._canvas_norm_center_widget(0.7, 0.7), 20, 10)
        assert _snapshot(canvas) != before
        assert canvas._dirty_hits >= 1

    def test_no_jump_when_crossing_threshold(self, canvas, qtbot):
        """跨过死区那一刻不能突跳：一步到位与分多步移动结果必须一致。

        死区实现成"未越界就不写数据"，越界后仍按 _drag_orig + 完整位移
        重算，所以不会丢掉死区内那几个像素、也不会多走一截。
        """
        one = RegionCanvas()
        qtbot.addWidget(one)
        one.resize(800, 600)
        one.set_image(np.zeros((600, 800, 3), dtype=np.uint8))
        one.set_regions([Region(key="foo", x_ratio=0.2, y_ratio=0.2,
                                w_ratio=0.3, h_ratio=0.3)])
        _drag(one, _region_center(one), 20, 10, steps=1)
        _drag(canvas, _region_center(canvas), 20, 10, steps=20)
        assert one.get_regions()[0].to_dict() == canvas.get_regions()[0].to_dict()
