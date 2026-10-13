"""隐藏全部视图必须持久化，并同步当前编辑器与重新打开后的视图。"""

import yaml
from PyQt6.QtWidgets import QCheckBox, QDialog

from lvjiang.core import scene_registry
from lvjiang.core.scene_definition import SceneRegistry
from lvjiang.core.scene_definition_models import RegionDef
from lvjiang.ui.scene_editor.scene_tab import SceneTab
from tests.ui.test_scene_editor_reload import editor as editor


def test_scene_manage_hides_all_and_reopens_base(editor, qtbot, monkeypatch):
    dialog, _system, _manager = editor
    registry = scene_registry.get_registry()
    registry.enable_scene_views("sample")
    registry.add_scene_view("sample", "shop", "商店")
    scene = registry.get_scene("sample")
    scene.regions = [RegionDef(key="entry", name="入口"),
                     RegionDef(key="buy", name="购买", views=["shop"])]
    registry.save_scene_views("sample")
    tab = dialog._tabs["sample"]
    tab._refresh_view_combo()
    tab._view_combo.setCurrentIndex(tab._view_combo.findData("shop"))

    accepted = False

    def manage(window):
        checkbox = next(c for c in window.findChildren(QCheckBox)
                        if c.text() == "隐藏全部视图")
        assert checkbox.isEnabled()
        checkbox.setChecked(True)
        return QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", manage)
    tab._btn_scene_manage.click()
    assert not scene.hide_all_views
    assert tab.current_view == "shop"

    accepted = True
    tab._btn_scene_manage.click()
    assert scene.hide_all_views
    assert tab._view_combo.findData("") == -1
    assert tab.current_view == "base"
    assert tab._region_table.rowCount() == 1
    assert tab._canvas._visible_keys == {"entry"}
    saved_path = registry._resolver.resolve_read("scenes/sample.yaml")
    saved = yaml.safe_load(saved_path.read_text(encoding="utf-8"))
    assert saved["hide_all_views"] is True

    reloaded = SceneRegistry(resolver=registry._resolver)
    monkeypatch.setattr(scene_registry, "_registry", reloaded)
    reopened = SceneTab("sample")
    qtbot.addWidget(reopened)
    assert reopened.current_view == "base"
    assert reopened._view_combo.findData("") == -1
    assert reopened._canvas._visible_keys == {"entry"}
    assert reloaded.get_scene("sample").regions[1].views == ["shop"]
    reopened._view_combo.setCurrentIndex(reopened._view_combo.findData("shop"))
    assert reopened.current_view == "shop"
    assert reopened._canvas._visible_keys == {"buy"}

    def unhide(window):
        next(c for c in window.findChildren(QCheckBox)
             if c.text() == "隐藏全部视图").setChecked(False)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", unhide)
    reopened._btn_scene_manage.click()
    assert reopened._view_combo.findData("") >= 0
    assert reopened.current_view == "shop"
    reopened._view_combo.setCurrentIndex(reopened._view_combo.findData(""))
    assert reopened._canvas._visible_keys is None
    assert reopened._region_table.rowCount() == 2
    saved = yaml.safe_load(saved_path.read_text(encoding="utf-8"))
    assert "hide_all_views" not in saved
