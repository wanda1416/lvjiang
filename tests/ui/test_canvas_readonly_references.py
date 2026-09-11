"""跨场景引用只开放位置拖动，其余编辑能力仍锁定。"""

from PyQt6.QtCore import QPoint, QRectF, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QMenu

from lvjiang.core.layout_models import Point, Region
from lvjiang.ui.scene_editor.canvas import RegionCanvas


def _drag(canvas: RegionCanvas, start: QPoint, end: QPoint) -> None:
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(canvas, end)
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=end)


def test_referenced_region_can_be_moved_and_restored(qtbot, monkeypatch):
    canvas = RegionCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(1000, 1000)
    canvas.show()
    canvas.set_regions([
        Region(
            "shared", 0.1, 0.1, 0.2, 0.2,
            source_scene="source", source_x_ratio=0.1, source_y_ratio=0.1,
        ),
    ])
    canvas._display_rect = QRectF(0, 0, 1000, 1000)
    changed = []
    canvas.on_region_changed = lambda: changed.append(True)

    _drag(canvas, QPoint(200, 200), QPoint(350, 350))

    region = canvas.get_regions()[0]
    assert (region.x_ratio, region.y_ratio, region.w_ratio, region.h_ratio) == (
        0.25, 0.25, 0.2, 0.2)
    assert region.position_overridden
    assert canvas._selected_idx == 0
    assert changed == [True]

    monkeypatch.setattr(QMenu, "exec", lambda menu, *_args: next(
        action for action in menu.actions() if action.text() == "还原位置"))
    canvas._show_context_menu(canvas._region_rect_widget(region).center())

    region = canvas.get_regions()[0]
    assert (region.x_ratio, region.y_ratio) == (0.1, 0.1)
    assert not region.position_overridden
    assert changed == [True, True]


def test_referenced_point_can_be_moved_and_restored_but_not_otherwise_edited(
    qtbot, monkeypatch,
):
    canvas = RegionCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(1000, 1000)
    canvas.show()
    canvas.set_points([
        Point(
            "shared", 0.4, 0.4, source_scene="source",
            source_x_ratio=0.4, source_y_ratio=0.4,
        ),
    ])
    canvas._display_rect = QRectF(0, 0, 1000, 1000)
    changed = []
    canvas.on_poi_changed = lambda: changed.append(True)

    _drag(canvas, QPoint(400, 400), QPoint(600, 600))
    canvas.begin_draw_arrow("shared")

    point = canvas.get_points()[0]
    assert (point.cx_ratio, point.cy_ratio, point.r_ratio) == (
        0.6, 0.6, 0.015)
    assert point.position_overridden
    assert canvas.delete_point_by_key("shared") is False
    assert canvas._selected_point_idx == 0
    assert canvas.get_points()[0].source_scene == "source"
    assert canvas._poi_action.name == "NONE"
    assert changed == [True]

    monkeypatch.setattr(QMenu, "exec", lambda menu, *_args: next(
        action for action in menu.actions() if action.text() == "还原位置"))
    assert canvas._poi_handle_context_menu(
        canvas._point_center_widget(point))

    point = canvas.get_points()[0]
    assert (point.cx_ratio, point.cy_ratio) == (0.4, 0.4)
    assert not point.position_overridden
    assert changed == [True, True]
