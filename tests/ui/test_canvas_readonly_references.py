"""跨场景展开坐标在画布中只读。"""

from PyQt6.QtCore import QPoint, QRectF, Qt
from PyQt6.QtTest import QTest

from lvjiang.core.layout_models import Point, Region
from lvjiang.ui.scene_editor.canvas import RegionCanvas


def _drag(canvas: RegionCanvas, start: QPoint, end: QPoint) -> None:
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(canvas, end)
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=end)


def test_referenced_region_can_be_drawn_and_selected_but_not_moved(qtbot):
    canvas = RegionCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(1000, 1000)
    canvas.show()
    canvas.set_regions([
        Region("shared", 0.1, 0.1, 0.2, 0.2, source_scene="source"),
    ])
    canvas._display_rect = QRectF(0, 0, 1000, 1000)
    changed = []
    canvas.on_region_changed = lambda: changed.append(True)

    _drag(canvas, QPoint(200, 200), QPoint(350, 350))

    region = canvas.get_regions()[0]
    assert (region.x_ratio, region.y_ratio, region.w_ratio, region.h_ratio) == (
        0.1, 0.1, 0.2, 0.2)
    assert canvas._selected_idx == 0
    assert changed == []


def test_referenced_point_cannot_be_moved_resized_deleted_or_used_for_arrow(
    qtbot,
):
    canvas = RegionCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(1000, 1000)
    canvas.show()
    canvas.set_points([
        Point("shared", 0.4, 0.4, source_scene="source"),
    ])
    canvas._display_rect = QRectF(0, 0, 1000, 1000)
    changed = []
    canvas.on_poi_changed = lambda: changed.append(True)

    _drag(canvas, QPoint(400, 400), QPoint(600, 600))
    canvas.begin_draw_arrow("shared")

    point = canvas.get_points()[0]
    assert (point.cx_ratio, point.cy_ratio, point.r_ratio) == (
        0.4, 0.4, 0.015)
    assert canvas.delete_point_by_key("shared") is False
    assert canvas._selected_point_idx == 0
    assert canvas.get_points()[0].source_scene == "source"
    assert canvas._poi_action.name == "NONE"
    assert changed == []
