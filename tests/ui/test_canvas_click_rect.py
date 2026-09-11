"""场景画布上的 Region 点击范围标定。"""

import numpy as np
import pytest
from PyQt6.QtCore import QPoint, QRectF, Qt
from PyQt6.QtTest import QTest

from lvjiang.core.layout_models import Region, TemplateBinding
from lvjiang.ui.scene_editor.canvas import RegionCanvas
from lvjiang.ui.scene_editor.canvas_interaction import clamp_click_rect


def _canvas(qtbot, region: Region) -> RegionCanvas:
    canvas = RegionCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(1000, 1000)
    canvas.set_regions([region])
    canvas._display_rect = QRectF(0, 0, 1000, 1000)
    canvas.select_region(0)
    canvas.set_click_rect_mode()
    canvas._jitter_ratio = 0.25
    canvas.show()
    return canvas


def _drag(canvas: RegionCanvas, start: QPoint, end: QPoint) -> None:
    QTest.mousePress(canvas, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(canvas, end)
    QTest.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=end)


def test_draw_click_rect_is_relative_to_region_and_marks_dirty(qtbot):
    canvas = _canvas(qtbot, Region("btn", 0.1, 0.1, 0.4, 0.4))
    changed = []
    canvas.on_region_changed = lambda: changed.append(True)

    # 在 Region 左上四分之一区域框选；坐标应保存为相对 Region 的 0~1 比例。
    _drag(canvas, QPoint(120, 120), QPoint(260, 220))

    assert canvas.get_regions()[0].click_rect == pytest.approx(
        (0.05, 0.05, 0.35, 0.25))
    assert changed == [True]


def test_clear_click_rect_restores_derived_default(qtbot):
    canvas = _canvas(qtbot, Region(
        "btn", 0.1, 0.1, 0.4, 0.4,
        click_rect=(0.0, 0.0, 1.0, 0.5),
    ))
    changed = []
    canvas.on_region_changed = lambda: changed.append(True)

    canvas.clear_selected_click_rect()

    assert canvas.get_regions()[0].click_rect is None
    assert changed == [True]
    # 默认 0.25 半径对应 Region 中间一半。
    rect = canvas._click_rect_canvas(canvas._regions[0], 0.25)
    assert (rect.x(), rect.y(), rect.width(), rect.height()) == pytest.approx(
        (0.2, 0.2, 0.2, 0.2))


def test_referenced_region_click_rect_is_read_only(qtbot):
    original = Region(
        "shared", 0.1, 0.1, 0.4, 0.4,
        click_rect=(0.0, 0.0, 1.0, 0.5), source_scene="source",
    )
    canvas = _canvas(qtbot, original)
    changed = []
    canvas.on_region_changed = lambda: changed.append(True)

    _drag(canvas, QPoint(200, 150), QPoint(300, 300))
    canvas.clear_selected_click_rect()

    assert canvas.get_regions()[0].click_rect == original.click_rect
    assert changed == []


def test_click_rect_interaction_clamps_to_region():
    assert clamp_click_rect(-0.2, 0.9, 0.5, 0.5) == (0.0, 0.5, 0.5, 0.5)
    assert clamp_click_rect(0.99, 0.99, 0.001, 0.001) == (
        0.95, 0.95, 0.05, 0.05)


def test_template_crop_is_clipped_to_selected_region(qtbot):
    canvas = RegionCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(1000, 1000)
    image = np.zeros((1000, 1000, 3), dtype=np.uint8)
    image[100:500, 100:500] = np.arange(400, dtype=np.uint8)[:, None, None]
    canvas.set_image(image)
    canvas.set_regions([Region("logo", 0.1, 0.1, 0.4, 0.4)])
    canvas._display_rect = QRectF(0, 0, 1000, 1000)
    canvas.select_region(0)
    ready = []
    canvas.on_template_crop_ready = lambda *args: ready.append(args)
    canvas.show()

    assert canvas.begin_template_crop() is True
    # 终点越出 Region；实际裁剪必须在 Region 右下边界 500px 处停止。
    _drag(canvas, QPoint(200, 200), QPoint(700, 700))

    assert len(ready) == 1
    key, crop, record_w, record_h = ready[0]
    assert key == "logo"
    assert crop.shape[:2] == (300, 300)
    assert (record_w, record_h) == (1000, 1000)


def test_capture_whole_region_ignores_click_rect(qtbot):
    canvas = RegionCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(1000, 1000)
    image = np.zeros((1000, 1000, 3), dtype=np.uint8)
    canvas.set_image(image)
    canvas.set_regions([
        Region(
            "logo", 0.1, 0.2, 0.4, 0.3,
            click_rect=(0.25, 0.25, 0.5, 0.5),
        ),
    ])
    canvas._display_rect = QRectF(0, 0, 1000, 1000)
    canvas.select_region(0)
    ready = []
    canvas.on_template_crop_ready = lambda *args: ready.append(args)

    assert canvas.capture_selected_region_template() is True

    _key, crop, record_w, record_h = ready[0]
    assert crop.shape[:2] == (300, 400)
    assert (record_w, record_h) == (1000, 1000)


def test_canvas_template_binding_is_region_only_and_marks_dirty(qtbot):
    canvas = _canvas(qtbot, Region("logo", 0.1, 0.1, 0.4, 0.4))
    changed = []
    canvas.on_region_changed = lambda: changed.append(True)
    binding = TemplateBinding("layout/scene/logo", 0.9)

    assert canvas.set_selected_template(binding) is True

    assert canvas.get_regions()[0].template == binding
    assert changed == [True]


def test_recropping_same_template_binding_still_marks_dirty(qtbot):
    binding = TemplateBinding("layout/scene/logo", 0.9)
    canvas = _canvas(qtbot, Region(
        "logo", 0.1, 0.1, 0.4, 0.4, template=binding))
    changed = []
    canvas.on_region_changed = lambda: changed.append(True)

    assert canvas.set_selected_template(binding, force_changed=True) is True

    assert changed == [True]
