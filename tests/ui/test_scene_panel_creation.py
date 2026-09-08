"""场景网格定义创建。"""

from unittest.mock import Mock

from lvjiang.core.scene_definition import PanelDef
from lvjiang.ui.scene_editor.scene_panel_editor import PanelEditorMixin


def test_new_panel_accepts_full_edit_dialog_result(monkeypatch):
    """编辑弹窗返回定义、场景、行列四项；新建路径不能按旧的两项解包。"""
    panel = PanelDef(key="grid", name="网格")
    host = Mock()
    host._scene_key = "scene"
    host._show_panel_edit_dialog.return_value = (
        panel, "scene", 3, 6)
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
