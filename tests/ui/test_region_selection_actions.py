"""列表刷新和弹出菜单之后，复制/删除仍应作用于选中的区域。"""

from unittest.mock import Mock

import numpy as np
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QInputDialog,
    QMenu,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from lvjiang.core.layout_models import Region
from lvjiang.core.scene_definition import RegionDef, SceneDef, SceneRefDef
from lvjiang.ui.scene_editor.canvas import RegionCanvas
from lvjiang.ui.scene_editor.scene_region_panel import RegionPanelMixin


class RegionHost(RegionPanelMixin, QWidget):
    def __init__(self):
        super().__init__()
        self._scene_key = "scene"
        self._current_view = ""
        self._on_add_scene_reference = Mock()
        self._canvas = RegionCanvas()
        outer = QVBoxLayout(self)
        outer.addWidget(self._canvas)
        outer.addWidget(self._build_region_panel())
        self._canvas.on_selection_changed = self._refresh_region_list
        self._canvas.on_region_changed = self._refresh_region_list


@pytest.fixture
def region_host(qtbot, monkeypatch):
    registry = Mock()
    scene = SceneDef("scene", "Scene", regions=[
        RegionDef(f"r{i}", f"Region {i}") for i in range(6)])
    registry.get_scene.return_value = scene
    monkeypatch.setattr(
        "lvjiang.ui.scene_editor.scene_region_panel.get_registry", lambda: registry)
    widget = RegionHost()
    widget.registry = registry
    widget._refresh_lists = widget._refresh_region_list
    qtbot.addWidget(widget)
    widget.resize(1000, 850)
    widget._canvas.set_image(np.zeros((400, 1000, 3), dtype=np.uint8))
    widget._canvas.set_regions([
        Region(f"r{i}", .02 + .18 * i, .2, .10 + .01 * i, .2)
        for i in range(5)])
    widget._canvas.set_current_regions([(r.key, r.name) for r in scene.regions])
    widget._refresh_region_list()
    widget.show()
    qtbot.waitExposed(widget)
    return widget


@pytest.mark.parametrize("index,action", [(3, "复制"), (4, "删除")])
def test_region_menu_keeps_clicked_target_after_table_focus(
        region_host, qtbot, monkeypatch, index, action):
    host = region_host
    canvas = host._canvas
    before = canvas.get_regions()
    # 用户先在表格选中，再到画布右键；失去焦点期间可能刷新列表。
    host._region_table.selectRow(index)
    qtbot.mouseClick(canvas, Qt.MouseButton.LeftButton,
                     pos=canvas._region_rect_widget(before[index]).center().toPoint())
    assert canvas._regions[canvas._selected_idx].key == before[index].key

    def menu_exec(menu, *args):
        # 模拟菜单嵌套事件循环中，另一列表动作改变了当前选择。
        host._region_table.setCurrentCell(0, 0)
        host._region_table.setFocus()
        QApplication.processEvents()
        return next(a for a in menu.actions() if a.text() == action)

    def choose_field(dialog):
        return 1

    monkeypatch.setattr(QMenu, "exec", menu_exec)
    monkeypatch.setattr(QInputDialog, "exec", choose_field)
    monkeypatch.setattr(QInputDialog, "textValue", lambda self: "Region 5 (r5)")
    canvas._show_context_menu(canvas._region_rect_widget(before[index]).center())
    after = canvas.get_regions()
    assert after[0] == before[0]
    if action == "删除":
        assert [r.key for r in after] == [r.key for i, r in enumerate(before) if i != index]
    else:
        copied = next(r for r in after if r.key == "r5")
        assert (copied.x_ratio, copied.y_ratio, copied.w_ratio, copied.h_ratio) == (
            before[index].x_ratio, before[index].y_ratio,
            before[index].w_ratio, before[index].h_ratio)


def test_refresh_preserves_fourth_region_identity(region_host):
    host = region_host
    host._region_table.selectRow(3)
    host._refresh_region_list()
    assert host._region_table.currentRow() == 3
    assert host._canvas.selected_region_key() == "r3"


def test_menu_target_removed_during_popup_never_deletes_first_region(
        region_host, monkeypatch):
    canvas = region_host._canvas
    canvas.select_region(4)
    first = canvas.get_regions()[0]

    def execute(menu, *args):
        canvas.set_regions(canvas.get_regions()[:-1])
        canvas.select_region(0)
        return next(action for action in menu.actions() if action.text() == "删除")

    monkeypatch.setattr(QMenu, "exec", execute)
    canvas._show_context_menu(canvas._region_rect_widget(canvas.get_regions()[4]).center())
    assert canvas.get_regions()[0] == first
    assert len(canvas.get_regions()) == 4


@pytest.mark.parametrize("placed", [True, False])
@pytest.mark.parametrize("confirmed", [True, False])
def test_reference_button_unlinks_only_current_scene(
        region_host, monkeypatch, placed, confirmed):
    host = region_host
    scene = host.registry.get_scene("scene")
    ref = SceneRefDef(scene="source", entity="shared")
    other = SceneRefDef(scene="source", entity="other")
    scene.references = [ref, other]
    monkeypatch.setattr(
        "lvjiang.ui.scene_editor.scene_region_panel.get_region_def",
        lambda source, key: RegionDef(key, key))
    monkeypatch.setattr(
        "lvjiang.ui.scene_editor.scene_region_panel.get_scene_name", lambda key: key)
    monkeypatch.setattr(
        "lvjiang.ui.scene_editor.scene_region_panel.sync_scene_cache", lambda key: None)
    purge = Mock()
    monkeypatch.setattr(
        "lvjiang.ui.scene_editor.scene_region_panel.delete_item_key_across_all_layouts", purge)
    host.on_scene_reference_removed = Mock()
    before = host._canvas.get_regions()
    if placed:
        host._canvas.set_regions(before + [
            Region("shared", .1, .1, .2, .2, source_scene="source"),
            Region("other", .3, .3, .2, .2, source_scene="source"),
        ])
    host._refresh_region_list()
    host._region_table.selectRow(6)
    assert host._btn_del_region.text() == "解除引用"
    host._refresh_region_list()
    assert host._btn_del_region.text() == "解除引用"
    host._region_table.selectRow(0)
    assert host._btn_del_region.text() == "删除区域"
    host._region_table.selectRow(6)

    def remove(current, source, entity):
        assert (current, source, entity) == ("scene", "source", "shared")
        scene.references = [r for r in scene.references if r is not ref]

    host.registry.remove_scene_reference.side_effect = remove
    monkeypatch.setattr(QMessageBox, "question", lambda *args:
                        QMessageBox.StandardButton.Yes if confirmed else QMessageBox.StandardButton.No)
    host._btn_del_region.click()
    if confirmed:
        host.registry.remove_scene_reference.assert_called_once_with("scene", "source", "shared")
        host.on_scene_reference_removed.assert_called_once_with("scene", "source", "shared")
        assert scene.references == [other]
        assert all(r.key != "shared" for r in host._canvas.get_regions())
        assert (any(r.key == "other" for r in host._canvas.get_regions())) == placed
    else:
        host.registry.remove_scene_reference.assert_not_called()
        host.on_scene_reference_removed.assert_not_called()
        assert scene.references == [ref, other]
    assert host._canvas.get_regions()[:5] == before
    host.registry.remove_region_from_scene.assert_not_called()
    purge.assert_not_called()
