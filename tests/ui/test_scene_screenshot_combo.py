"""场景编辑器截图选择器与布局绑定保持同步。"""

from types import SimpleNamespace

import numpy as np

from lvjiang.ui.scene_editor import scene_tab as scene_tab_module
from lvjiang.ui.scene_editor.dialog import SceneEditorDialog
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


def test_subscene_keeps_screenshot_controls_and_switches_images(
    qtbot, monkeypatch,
):
    monkeypatch.setattr(
        scene_tab_module, "list_scene_screenshots",
        lambda _layout, _scene, _view: [1, 2],
    )
    monkeypatch.setattr(
        scene_tab_module, "get_active_screenshot_index",
        lambda _layout, _scene, _view: 2,
    )
    saved = []
    monkeypatch.setattr(
        scene_tab_module, "set_active_screenshot_index",
        lambda *args: saved.append(args),
    )
    monkeypatch.setattr(SceneTab, "_refresh_version_info", lambda _self: None)
    tab = SceneTab("jianghu_card")
    qtbot.addWidget(tab)
    tab.set_layout_name("desktop")

    assert tab._view_combo.isHidden()
    assert not tab._screenshot_label.isHidden()
    assert not tab._screenshot_combo.isHidden()
    assert not tab._btn_manage_screenshots.isHidden()
    assert tab._screenshot_combo.isEnabled()
    assert tab.current_screenshot_index == 2

    changed = []
    tab.on_screenshot_changed = lambda *args: changed.append(args)
    tab._screenshot_combo.setCurrentIndex(0)

    assert saved == [("desktop", "jianghu_card", "", 1)]
    assert changed == [("jianghu_card", "", 1)]


def test_managing_screenshots_invalidates_only_that_scene_cache():
    current = ("desktop", "jianghu_card", "")
    other = ("desktop", "activity_jianghu", "")
    host = SimpleNamespace(
        _current_layout=SimpleNamespace(key="desktop"),
        _img_cache={(*current, 1): object(), (*current, 2): object(),
                    (*other, 1): object()},
    )

    SceneEditorDialog._on_tab_screenshot_set_changed(
        host, "jianghu_card", "")

    assert list(host._img_cache) == [(*other, 1)]


def test_subscene_screenshot_switch_refocuses_crop(qtbot, monkeypatch):
    tab = SceneTab("jianghu_card")
    qtbot.addWidget(tab)
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    calls = []
    monkeypatch.setattr(tab.canvas, "set_image", lambda _img: calls.append("set"))
    monkeypatch.setattr(tab.canvas, "focus_canvas", lambda: calls.append("focus"))
    host = SimpleNamespace(
        _current_layout=SimpleNamespace(key="desktop"),
        _tabs={"jianghu_card": tab},
        _get_cached_screenshot=lambda *_args: frame,
        _update_info_label=lambda: None,
    )

    SceneEditorDialog._on_tab_screenshot_changed(
        host, "jianghu_card", "", 2)

    assert calls == ["set", "focus"]
