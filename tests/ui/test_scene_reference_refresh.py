"""布局编辑器保存时刷新跨场景引用画布。"""

from unittest.mock import Mock

from lvjiang.core.layout_models import Layout, Region
from lvjiang.ui.scene_editor.dialog import SceneEditorDialog
from lvjiang.ui.scene_editor.layout_ops import LayoutOpsMixin


def test_sync_before_save_rebuilds_scene_references():
    host = Mock()
    host._current_layout = Layout(name="测试")
    host._tabs = {}
    host._current_scene_tab.return_value = None

    LayoutOpsMixin._sync_loaded_tabs_to_current_layout(host)

    host._refresh_loaded_scene_references.assert_called_once_with()
    host._refresh_loaded_subscene_contents.assert_called_once_with()


def test_rebuilt_reference_is_pushed_to_loaded_target_tab(monkeypatch):
    refreshed = Region(
        "status", 0.2, 0.3, 0.1, 0.05,
        source_scene="equip_detail",
    )
    layout = Layout(name="测试", regions={
        "equip_weapon_detail": [],
    })
    target_tab = Mock()
    host = Mock()
    host._current_layout = layout
    host._tabs = {"equip_weapon_detail": target_tab}

    def refresh(current_layout):
        current_layout.set_scene_regions(
            "equip_weapon_detail", [refreshed])
        return {"equip_weapon_detail"}

    monkeypatch.setattr(
        "lvjiang.ui.scene_editor.dialog.refresh_scene_references",
        refresh,
    )

    SceneEditorDialog._refresh_loaded_scene_references(host)

    pushed = target_tab.set_regions.call_args.args[0]
    assert len(pushed) == 1
    assert pushed[0].key == "status"
    assert pushed[0].source_scene == "equip_detail"
    target_tab.set_points.assert_called_once_with([])
