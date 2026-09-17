"""场景编辑器截图选择器与布局绑定保持同步。"""

from lvjiang.ui.scene_editor import scene_tab as scene_tab_module
from lvjiang.ui.scene_editor.scene_tab import SceneTab


def test_layout_binding_populates_existing_default_screenshot(qtbot, monkeypatch):
    monkeypatch.setattr(
        scene_tab_module,
        "list_scene_screenshots",
        lambda layout, _scene, _view: [1] if layout == "android" else [2, 3],
    )
    monkeypatch.setattr(
        scene_tab_module,
        "get_active_screenshot_index",
        lambda layout, _scene, _view: 1 if layout == "android" else 3,
    )
    monkeypatch.setattr(SceneTab, "_refresh_version_info", lambda _self: None)

    tab = SceneTab("general_move")
    qtbot.addWidget(tab)
    assert tab._screenshot_combo.count() == 0

    tab.set_layout_name("android")

    assert tab._screenshot_combo.count() == 1
    assert tab._screenshot_combo.currentText() == "截图 1"
    assert tab.current_screenshot_index == 1

    # 切换布局时必须重建，而不是继续显示上一布局的截图 1。
    tab.set_layout_name("desktop")
    assert [
        tab._screenshot_combo.itemText(index)
        for index in range(tab._screenshot_combo.count())
    ] == ["截图 2", "截图 3"]
    assert tab._screenshot_combo.currentText() == "截图 3"
    assert tab.current_screenshot_index == 3
