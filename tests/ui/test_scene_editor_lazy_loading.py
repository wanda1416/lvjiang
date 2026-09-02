"""场景管理启动只构建当前页，其他重量级编辑器按需创建。"""

from PyQt6.QtCore import QEvent, QObject
from PyQt6.QtWidgets import QApplication, QStyle, QStyleOptionComboBox, QWidget

from lvjiang.core.layout_models import CanvasConfig, Region
from lvjiang.core.scene_registry import get_registry, is_subscene
from lvjiang.ui.scene_editor.dialog import (
    _REFERENCE_GROUP_COMBO_CHARACTER_CAPACITY,
    SceneEditorDialog,
)


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


def test_lazy_scene_tab_does_not_flash_orphan_toolbar_windows(
        qtbot, monkeypatch):
    """普通场景工具栏完成挂载前，不能把子控件临时 show 成顶层窗口。"""
    monkeypatch.setattr(
        SceneEditorDialog, "_save_window_size", lambda self: None)
    dialog = SceneEditorDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    QApplication.processEvents()

    target = next(
        key for key in get_registry().all_scene_keys()
        if key not in dialog._tabs and not is_subscene(key)
    )
    orphan_windows: list[QWidget] = []

    class TopLevelShowWatcher(QObject):
        def eventFilter(self, obj, event):  # type: ignore[override]
            if (event.type() == QEvent.Type.Show
                    and isinstance(obj, QWidget) and obj.isWindow()):
                orphan_windows.append(obj)
            return False

    watcher = TopLevelShowWatcher()
    app = QApplication.instance()
    assert app is not None
    app.installEventFilter(watcher)
    try:
        dialog._select_scene(target)
        QApplication.processEvents()
    finally:
        app.removeEventFilter(watcher)

    assert target in dialog._tabs
    assert orphan_windows == []


def test_reference_groups_stay_lazy_until_dropdown_is_used(qtbot, monkeypatch):
    monkeypatch.setattr(
        SceneEditorDialog, "_save_window_size", lambda self: None)

    dialog = SceneEditorDialog()
    qtbot.addWidget(dialog)

    assert dialog._combo_ref_group._loaded is False
    assert dialog._combo_ref_group.count() == 1

    combo = dialog._combo_ref_group
    option = QStyleOptionComboBox()
    option.initFrom(combo)
    option.rect = combo.rect()
    option.rect.setWidth(combo.minimumWidth())
    content = combo.style().subControlRect(
        QStyle.ComplexControl.CC_ComboBox,
        option,
        QStyle.SubControl.SC_ComboBoxEditField,
        combo,
    )
    expected = combo.fontMetrics().horizontalAdvance(
        "汉" * _REFERENCE_GROUP_COMBO_CHARACTER_CAPACITY
    )
    assert content.width() >= expected


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


def test_save_subscene_refreshes_loaded_parent_preview(qtbot, monkeypatch):
    """子场景保存进 Layout 后，已创建的父页不能继续持有旧投影。"""
    monkeypatch.setattr(
        SceneEditorDialog, "_save_window_size", lambda self: None)
    dialog = SceneEditorDialog()
    qtbot.addWidget(dialog)

    registry = get_registry()
    parent = next(
        scene for key in registry.all_scene_keys()
        if (scene := registry.get_scene(key)) is not None
        and scene.subscene_refs
    )
    ref_def = parent.subscene_refs[0]
    child_key = ref_def.scene

    dialog._select_scene(parent.key)
    dialog._select_scene(child_key)
    parent_tab = dialog._tabs[parent.key]
    child_tab = dialog._tabs[child_key]
    before = parent_tab.canvas._subscene_contents[ref_def.key]["regions"][0]

    changed = child_tab.get_regions()
    changed[0] = Region(
        key=changed[0].key,
        x_ratio=min(0.99, changed[0].x_ratio + 0.123),
        y_ratio=changed[0].y_ratio,
        w_ratio=changed[0].w_ratio,
        h_ratio=changed[0].h_ratio,
        disabled=changed[0].disabled,
    )
    child_tab.set_regions(changed)
    dialog._on_scene_data_changed(child_key)
    monkeypatch.setattr(dialog._manager, "save_layout", lambda *args, **kwargs: True)

    dialog._on_save_layout()
    dialog._select_scene(parent.key)

    after = parent_tab.canvas._subscene_contents[ref_def.key]["regions"][0]
    assert after.x_ratio == changed[0].x_ratio
    assert after.x_ratio != before.x_ratio


def test_reapply_layout_keeps_subscene_crop_canvas(qtbot, monkeypatch):
    """重新应用布局时，子场景不能被主布局画布覆盖成全屏。"""
    monkeypatch.setattr(
        SceneEditorDialog, "_save_window_size", lambda self: None)
    dialog = SceneEditorDialog()
    qtbot.addWidget(dialog)

    child_key = next(
        key for key in get_registry().all_scene_keys() if is_subscene(key))
    dialog._select_scene(child_key)
    assert dialog._current_layout is not None

    crop = CanvasConfig(
        x_ratio=0.13, y_ratio=0.17, w_ratio=0.61, h_ratio=0.57)
    dialog._current_layout.set_scene_crop_canvas(child_key, crop)
    dialog._apply_layout_to_tabs()

    actual = dialog._tabs[child_key].get_canvas_config()
    assert actual.to_dict() == crop.to_dict()


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
