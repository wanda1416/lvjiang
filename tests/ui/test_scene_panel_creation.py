"""场景网格定义创建。"""

from unittest.mock import Mock

from lvjiang.core.scene_definition import PanelDef
from lvjiang.ui.scene_editor.scene_panel_editor import PanelEditorMixin


def test_new_panel_creates_only_scene_definition(monkeypatch):
    """创建定义不接收布局参数，也不创建布局绑定。"""
    panel = PanelDef(key="grid", name="网格")
    host = Mock()
    host._scene_key = "scene"
    host._show_panel_definition_dialog.return_value = (
        panel, "scene")
    registry = Mock()
    monkeypatch.setattr(
        "lvjiang.ui.scene_editor.scene_panel_editor.get_registry",
        lambda: registry,
    )
    sync = Mock()
    monkeypatch.setattr(
        "lvjiang.ui.scene_editor.scene_panel_editor.sync_scene_cache",
        sync,
    )

    PanelEditorMixin._on_new_panel_def(host)

    registry.add_panel_to_scene.assert_called_once_with("scene", panel)
    sync.assert_called_once_with("scene")
    host._refresh_lists.assert_called_once_with()

    host._canvas.begin_place_panel.assert_not_called()
