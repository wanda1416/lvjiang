"""网格定义与布局绑定的保存边界和真实画布交互。"""

from unittest.mock import Mock

import numpy as np
import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QSpinBox,
    QTabWidget,
    QWidget,
)

from lvjiang.core.config.resolver import ConfigResolver
from lvjiang.core.layout_models import Panel
from lvjiang.core.scene_definition import PanelDef, SceneDef, SceneRegistry
from lvjiang.ui.scene_editor.canvas import RegionCanvas
from lvjiang.ui.scene_editor.panel_binding_form import (
    PanelBindingConfig,
    PanelBindingForm,
)
from lvjiang.ui.scene_editor.scene_panel_editor import PanelEditorMixin


class Host(PanelEditorMixin, QWidget):
    def __init__(self):
        super().__init__()
        self._scene_key = "scene"
        self._layout_name = "desktop"
        self._layout_rel_path = ""
        self._current_view = ""
        self._canvas = RegionCanvas()
        self._canvas.setParent(self)
        self._canvas.on_panel_changed = Mock()
        self._refresh_lists = Mock()
        self.on_item_migrated = Mock()


@pytest.fixture
def host(qtbot, tmp_path, monkeypatch):
    registry = SceneRegistry(resolver=ConfigResolver(
        system_dir=tmp_path / "system", local_dir=tmp_path / "local", dev_mode=True))
    registry._scenes = {
        "scene": SceneDef(key="scene", name="场景", panels=[PanelDef("grid", "网格")]),
        "target": SceneDef(key="target", name="目标"),
    }
    for module in ("scene_panel_editor", "scene_select"):
        monkeypatch.setattr(
            f"lvjiang.ui.scene_editor.{module}.get_registry", lambda: registry)
    monkeypatch.setattr(
        "lvjiang.ui.scene_editor.scene_select.get_scene_views", lambda key: [])
    monkeypatch.setattr(
        "lvjiang.ui.scene_editor.scene_panel_editor.sync_scene_cache", lambda key: None)
    widget = Host()
    qtbot.addWidget(widget)
    widget.registry = registry
    return widget


def test_create_definition_form_has_no_layout_fields(host, monkeypatch):
    def execute(dialog):
        assert not dialog.findChildren(QSpinBox)
        key, name = dialog.findChildren(QLineEdit)
        key.setText("new_grid")
        name.setText("新网格")
        dialog.findChild(QDialogButtonBox).button(
            QDialogButtonBox.StandardButton.Ok).click()
        return dialog.result()

    monkeypatch.setattr(QDialog, "exec", execute)
    host._on_new_panel_def()
    assert host.registry.get_scene("scene").panels[-1] == PanelDef("new_grid", "新网格")
    assert host._canvas.get_panels() == []
    host._canvas.on_panel_changed.assert_not_called()


def test_binding_preserves_parameters_and_replaces_disabled_placeholder(host, monkeypatch, qtbot):
    host._canvas.resize(500, 500)
    host._canvas.set_image(np.zeros((500, 500, 3), dtype=np.uint8))
    host.show()
    host._canvas.show()
    qtbot.waitUntil(lambda: host._canvas._display_rect.width() > 0)
    host._canvas.set_panels([Panel("grid", 0, 0, 0, 0, disabled=True)])

    def execute(dialog):
        form = dialog.findChild(PanelBindingForm)
        form.rows.setValue(1)
        form.cols.setValue(9)
        form.direction.setCurrentIndex(form.direction.findData("none"))
        form.calibration.setCurrentIndex(form.calibration.findData("even"))
        form.visible.setValue(0.82)
        form.disabled.setChecked(False)
        dialog.findChild(QDialogButtonBox).button(
            QDialogButtonBox.StandardButton.Ok).click()
        return dialog.result()

    monkeypatch.setattr(QDialog, "exec", execute)
    host._bind_panel_key("grid")
    assert len(host._canvas.get_panels()) == 1
    host._canvas.on_panel_changed.assert_not_called()
    canvas = host._canvas
    canvas._panel_drag_start = canvas._norm_to_widget(0.1, 0.2)
    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease, canvas._norm_to_widget(0.8, 0.7),
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    canvas.mouseReleaseEvent(event)
    panels = canvas.get_panels()
    assert len(panels) == 1
    panel = panels[0]
    assert (panel.rows, panel.cols, panel.calibration, panel.scroll_direction) == (1, 9, "even", "none")
    assert panel.min_visible == pytest.approx(0.82)
    assert not panel.disabled
    assert panel.w_ratio > 0 and panel.h_ratio > 0
    canvas.on_panel_changed.assert_called_once()
    assert host.registry.get_scene("scene").panels == [PanelDef("grid", "网格")]


def test_cancel_placement_leaves_no_binding_or_dirty_state(host):
    host._canvas.begin_place_panel(PanelDef("grid", "网格"), PanelBindingConfig(rows=7))
    host._canvas.cancel_panel_place()
    assert host._canvas.get_panels() == []
    assert host._canvas._pending_panel_config is None
    host._canvas.on_panel_changed.assert_not_called()


def test_edit_binding_reads_layout_values_and_does_not_save_definition(host, monkeypatch):
    original = Panel("grid", .1, .1, .6, .7, rows=7, cols=5,
                     min_visible=.82, calibration="image", scroll_direction="both")
    host._canvas.set_panels([original])
    before = host.registry.get_scene("scene").panels[0]
    save_definition = Mock()
    host.registry._save_scene_yaml = save_definition

    def execute(dialog):
        tabs = dialog.findChild(QTabWidget)
        assert tabs.currentIndex() == 1
        assert "场景定义" in tabs.tabText(0)
        assert "布局" in tabs.tabText(1)
        form = dialog.findChild(PanelBindingForm)
        assert form.value() == PanelBindingConfig.from_panel(original)
        form.rows.setValue(8)
        dialog.findChild(QDialogButtonBox).button(
            QDialogButtonBox.StandardButton.Save).click()
        return dialog.result()

    monkeypatch.setattr(QDialog, "exec", execute)
    host._show_panel_properties("grid", layout_first=True)
    assert host._canvas.get_panels()[0].rows == 8
    assert original.rows == 7
    assert host.registry.get_scene("scene").panels[0] == before
    save_definition.assert_not_called()


def test_definition_edit_and_rename_preserve_layout_config(host, monkeypatch):
    original = Panel("grid", .1, .2, .3, .4, rows=8, min_visible=.78, calibration="even")
    host._canvas.set_panels([original])
    rename = Mock()
    monkeypatch.setattr(
        "lvjiang.ui.scene_editor.scene_panel_editor.rename_item_key_across_all_layouts", rename)
    host._save_panel_definition(
        host.registry.get_scene("scene").panels[0], PanelDef("renamed", "新名"), "scene")
    expected = original.clone()
    expected.key = "renamed"
    assert host._canvas.get_panels() == [expected]
    rename.assert_called_once_with("scene", "panel", "grid", "renamed")


def test_definition_name_edit_does_not_dirty_layout(host):
    panel = Panel("grid", .1, .2, .3, .4, rows=8)
    host._canvas.set_panels([panel])
    host._save_panel_definition(
        host.registry.get_scene("scene").panels[0], PanelDef("grid", "新名"), "scene")
    assert host._canvas.get_panels() == [panel]
    host._canvas.on_panel_changed.assert_not_called()


def test_move_and_rename_pass_the_renamed_binding_with_all_parameters(host, monkeypatch):
    from lvjiang.core.layout_manager import migrate_layout_item
    from lvjiang.core.layout_models import Layout

    original = Panel("grid", .1, .2, .3, .4, rows=7, calibration="image", min_visible=.83)
    host._canvas.set_panels([original])
    monkeypatch.setattr(
        "lvjiang.ui.scene_editor.scene_panel_editor.rename_item_key_across_all_layouts", Mock())
    destination = Layout("desktop")

    def migrate(kind, key, source, target):
        destination.set_scene_panels(source, host._canvas.get_panels())
        assert migrate_layout_item(destination, source, target, kind, key)
        host._canvas.set_panels(destination.get_scene_panels(source))

    host.on_item_migrated.side_effect = migrate
    host._save_panel_definition(
        host.registry.get_scene("scene").panels[0], PanelDef("moved", "迁移"), "target")
    expected = original.clone()
    expected.key = "moved"
    assert destination.get_scene_panels("target") == [expected]
    assert host._canvas.get_panels() == []
    assert host.registry.get_scene("scene").panels == []
    assert host.registry.get_scene("target").panels == [PanelDef("moved", "迁移")]


def test_unbind_keeps_scene_definition(host):
    host._canvas.set_panels([Panel("grid", .1, .2, .3, .4)])
    host._selected_panel_key = lambda: "grid"
    host._on_unbind_panel()
    assert host._canvas.get_panels() == []
    assert host.registry.get_scene("scene").panels == [PanelDef("grid", "网格")]
    host._canvas.on_panel_changed.assert_called_once()


def test_binding_validation_rejects_incompatible_rows_and_direction(qtbot):
    form = PanelBindingForm()
    qtbot.addWidget(form)
    form.rows.setValue(1)
    assert not form.validate()
    form.direction.setCurrentIndex(form.direction.findData("none"))
    assert form.validate()


def test_unbound_properties_offer_binding_without_fake_parameters(host, monkeypatch):
    def execute(dialog):
        assert not dialog.findChildren(PanelBindingForm)
        tabs = dialog.findChild(QTabWidget)
        tabs.setCurrentIndex(1)
        assert dialog.findChild(QDialogButtonBox).button(
            QDialogButtonBox.StandardButton.Save).text() == "绑定到当前布局"
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", execute)
    host._show_panel_properties("grid")
    assert host._canvas.get_panels() == []


def test_canvas_double_click_opens_layout_editor(qtbot):
    canvas = RegionCanvas()
    qtbot.addWidget(canvas)
    canvas.resize(500, 500)
    canvas.set_image(np.zeros((500, 500, 3), dtype=np.uint8))
    canvas.show()
    qtbot.waitUntil(lambda: canvas._display_rect.width() > 0)
    canvas.set_panels([Panel("grid", .1, .1, .5, .5)])
    canvas.on_panel_edit_requested = Mock()
    canvas.on_panel_changed = Mock()
    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonDblClick, QPointF(canvas._norm_to_widget(.3, .3)),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    canvas.mouseDoubleClickEvent(event)
    canvas.on_panel_edit_requested.assert_called_once_with("grid")
    canvas.on_panel_changed.assert_not_called()
