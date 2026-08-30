"""场景管理启动只构建当前页，其他重量级编辑器按需创建。"""

from PyQt6.QtWidgets import QApplication

from lvjiang.core.scene_registry import get_registry
from lvjiang.ui.scene_editor.dialog import SceneEditorDialog


def test_scene_tabs_are_created_on_first_visit(qtbot, monkeypatch):
    monkeypatch.setattr(
        SceneEditorDialog, "_save_window_size", lambda self: None)

    dialog = SceneEditorDialog()
    qtbot.addWidget(dialog)
    assert dialog.updatesEnabled() is False
    dialog.show()
    QApplication.processEvents()
    assert dialog.updatesEnabled() is True

    registry = get_registry()
    all_scene_keys = registry.all_scene_keys()
    assert len(all_scene_keys) > 1
    assert len(dialog._tabs) == 1
    assert dialog._get_current_scene_key() in dialog._tabs

    # 标题全部存在，但第二个场景在访问前仍只是轻量占位页。
    target = all_scene_keys[1]
    assert target not in dialog._tabs
    dialog._select_scene(target)
    QApplication.processEvents()

    assert target in dialog._tabs
    assert len(dialog._tabs) == 2
    assert dialog._current_scene_tab() is dialog._tabs[target]


def test_reference_groups_stay_lazy_until_dropdown_is_used(qtbot, monkeypatch):
    monkeypatch.setattr(
        SceneEditorDialog, "_save_window_size", lambda self: None)

    dialog = SceneEditorDialog()
    qtbot.addWidget(dialog)

    assert dialog._combo_ref_group._loaded is False
    assert dialog._combo_ref_group.count() == 1


def test_save_keeps_data_from_unvisited_scenes(qtbot, monkeypatch):
    monkeypatch.setattr(
        SceneEditorDialog, "_save_window_size", lambda self: None)
    dialog = SceneEditorDialog()
    qtbot.addWidget(dialog)

    assert dialog._current_layout is not None
    before = dialog._current_layout.to_dict()
    unloaded = next(
        scene_key for scene_key in before["scenes"]
        if scene_key not in dialog._tabs
    )
    captured = {}

    def save_layout(layout, **kwargs):
        captured["layout"] = layout.to_dict()
        captured["kwargs"] = kwargs
        return True

    monkeypatch.setattr(dialog._manager, "save_layout", save_layout)
    dialog._on_save_layout()

    assert unloaded not in dialog._tabs
    assert captured["layout"]["scenes"][unloaded] == before["scenes"][unloaded]


def test_save_as_snapshot_keeps_data_from_unvisited_scenes(qtbot, monkeypatch):
    """另存为走独立克隆路径，也不能只保存已经创建控件的场景。"""
    monkeypatch.setattr(
        SceneEditorDialog, "_save_window_size", lambda self: None)
    dialog = SceneEditorDialog()
    qtbot.addWidget(dialog)

    assert dialog._current_layout is not None
    before = dialog._current_layout.to_dict()
    unloaded = next(
        scene_key for scene_key in before["scenes"]
        if scene_key not in dialog._tabs
    )
    clone = dialog._clone_current_layout("测试副本")

    assert clone is not None
    assert clone.name == "测试副本"
    assert unloaded not in dialog._tabs
    assert clone.to_dict()["scenes"][unloaded] == before["scenes"][unloaded]


def test_scene_selection_uses_widget_identity_not_registry_position(
        qtbot, monkeypatch):
    """registry 顺序暂时脱节时，选中和懒加载仍指向控件自述的场景。"""
    import lvjiang.ui.scene_editor.scene_ops as scene_ops

    monkeypatch.setattr(
        SceneEditorDialog, "_save_window_size", lambda self: None)
    dialog = SceneEditorDialog()
    qtbot.addWidget(dialog)
    target = next(
        scene_key for scene_key in get_registry().all_scene_keys()
        if scene_key not in dialog._tabs
    )

    class StaleRegistry:
        def get_groups(self):
            raise AssertionError("不应按 registry 位置查找分组")

        def get_group_scenes(self, _group_key):
            raise AssertionError("不应按 registry 位置查找场景")

    monkeypatch.setattr(scene_ops, "get_registry", lambda: StaleRegistry())
    dialog._select_scene(target)
    QApplication.processEvents()

    assert dialog._get_current_scene_key() == target
    assert target in dialog._tabs
