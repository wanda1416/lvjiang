"""场景画布中区域、面板、子场景引用共用矩形对齐与引用命令。"""

import numpy as np
import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from lvjiang.core.layout_models import Panel, Region, SubsceneRef
from lvjiang.ui.scene_editor.canvas import RegionCanvas
from lvjiang.ui.scene_editor.canvas_interaction import CanvasInteractionMixin


@pytest.fixture
def canvas(qtbot):
    c = RegionCanvas()
    qtbot.addWidget(c)
    c.resize(800, 600)
    c.set_image(np.zeros((600, 800, 3), dtype=np.uint8))
    c.set_regions([
        Region(key="region", x_ratio=0.10, y_ratio=0.12,
               w_ratio=0.20, h_ratio=0.18),
    ])
    c.set_panels([
        Panel(key="grid", x_ratio=0.62, y_ratio=0.15,
              w_ratio=0.20, h_ratio=0.16),
    ])
    c.set_subscene_refs([
        SubsceneRef(key="card", x_ratio=0.43, y_ratio=0.48,
                    w_ratio=0.15, h_ratio=0.14),
    ])
    return c


def test_snap_targets_merge_all_visible_rect_types(canvas):
    xs, ys = canvas._collect_snap_targets("subscene_ref", 0)

    assert any(value == pytest.approx(0.10) for value in xs)  # region 左边
    assert any(value == pytest.approx(0.30) for value in xs)  # region 右边
    assert any(value == pytest.approx(0.62) for value in xs)  # panel 左边
    assert any(value == pytest.approx(0.31) for value in ys)  # panel 下边
    assert not any(value == pytest.approx(0.43) for value in xs)  # 引用自身
    assert not any(value == pytest.approx(0.48) for value in ys)


def test_subscene_ref_can_snap_to_region_boundary(canvas):
    canvas.set_panels([])
    ref = canvas._subscene_refs[0]
    ref.x_ratio = 0.304  # 距 region 右边 0.004，小于 6 widget 像素

    canvas._apply_move_snap(ref, "subscene_ref", 0)

    assert ref.x_ratio == pytest.approx(0.30)
    assert canvas._snap_lines_x == pytest.approx([0.30])


def test_region_can_snap_to_subscene_ref_boundary(canvas):
    canvas.set_panels([])
    region = canvas._regions[0]
    region.x_ratio = 0.426  # 距引用左边 0.004

    canvas._apply_move_snap(region, "region", 0)

    assert region.x_ratio == pytest.approx(0.43)
    assert canvas._snap_lines_x == pytest.approx([0.43])


def test_shift_disables_shared_resize_snap(canvas):
    snapped = canvas._snap_resize_edges(
        0.10, 0.12, 0.426, 0.30,
        moving_left=False, moving_right=True,
        moving_top=False, moving_bottom=False,
        exclude_kind="region", exclude_idx=0,
        min_size=0.01, shift_held=False,
    )
    unsnapped = canvas._snap_resize_edges(
        0.10, 0.12, 0.426, 0.30,
        moving_left=False, moving_right=True,
        moving_top=False, moving_bottom=False,
        exclude_kind="region", exclude_idx=0,
        min_size=0.01, shift_held=True,
    )

    assert snapped[2] == pytest.approx(0.43)
    assert unsnapped[2] == pytest.approx(0.426)
    assert canvas._snap_lines_x == []


def test_right_click_selected_ref_uses_reference_menu(canvas):
    canvas.select_subscene_ref_by_key("card")
    called = []
    canvas._show_subscene_ref_context_menu = lambda pos: called.append(pos)
    pos = canvas._subscene_ref_rect_widget(canvas._subscene_refs[0]).center()
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress, pos, pos,
        Qt.MouseButton.RightButton, Qt.MouseButton.RightButton,
        Qt.KeyboardModifier.NoModifier,
    )

    CanvasInteractionMixin.mousePressEvent(canvas, event)

    assert called == [pos]
    assert canvas._panning is False


def test_copy_and_unbind_subscene_ref(canvas):
    canvas.set_scene_key("parent")
    canvas.select_subscene_ref_by_key("card")
    changed = []
    canvas.on_subscene_ref_changed = lambda: changed.append(True)

    canvas._copy_subscene_ref_key()
    assert QApplication.clipboard().text() == "[parent].[card]"

    canvas._delete_selected_subscene_ref()
    assert canvas.get_subscene_refs() == []
    assert changed == [True]
