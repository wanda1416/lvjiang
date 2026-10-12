"""外部编辑后重载必须恢复新定义/坐标，且不能改主页面的活动布局。"""

import json
import os

import pytest
from PyQt6.QtWidgets import QMessageBox

from lvjiang.core import scene_registry
from lvjiang.core.config import resolver as resolver_module
from lvjiang.core.config.resolver import ConfigResolver
from lvjiang.core.layout_manager import LayoutConfigManager
from lvjiang.core.scene_config import load_scene_manifest
from lvjiang.core.scene_definition import SceneRegistry
from lvjiang.ui.scene_editor.dialog import SceneEditorDialog


@pytest.fixture
def editor(qtbot, tmp_path, monkeypatch):
    system = tmp_path / "system"
    (system / "scenes").mkdir(parents=True)
    (system / "layouts" / "phone").mkdir(parents=True)
    (system / "scenes.yaml").write_text(
        "schema_version: 2\nscenes:\n  general:\n    name: 通用\n    items: [sample]\n",
        encoding="utf-8",
    )
    (system / "layouts.yaml").write_text(
        "schema_version: 2\nlayouts:\n  phone:\n    name: 手机\n"
        "  main_layout:\n    name: 主页面布局\n",
        encoding="utf-8",
    )
    (system / "scenes" / "sample.yaml").write_text(
        "key: sample\nname: 示例\nregions: []\n", encoding="utf-8",
    )
    # 磁盘上已有新坐标，但旧场景定义没有它，初次加载会过滤。
    (system / "layouts" / "phone" / "sample.json").write_text(
        json.dumps({"regions": [{"key": "new_button", "x_ratio": 0.1,
                                "y_ratio": 0.2, "w_ratio": 0.3, "h_ratio": 0.1}]}),
        encoding="utf-8",
    )
    resolver = ConfigResolver(system, tmp_path / "local", dev_mode=False)
    monkeypatch.setattr(resolver_module, "_resolver", resolver)
    manifest = load_scene_manifest(resolver)
    monkeypatch.setattr(scene_registry, "_registry", SceneRegistry(
        resolver=resolver, scene_order=manifest.order, group_config=manifest.groups,
        group_names=manifest.group_names,
    ))
    # 注册表全局派生字典同样隔离，不能污染其他用例。
    for name in ("SCENE_REGIONS", "SCENE_POINTS", "SCENE_PANELS",
                 "SCENE_GROUPS_META", "GROUP_SCENES"):
        monkeypatch.setattr(scene_registry, name, {})
    monkeypatch.setattr(scene_registry, "GROUP_ORDER", [])
    scene_registry._rebuild_scene_globals()
    manager = LayoutConfigManager()
    manager.set_active_layout("main_layout")
    dialog = SceneEditorDialog(layout_manager=manager, initial_layout_key="phone")
    qtbot.addWidget(dialog)
    # 关闭测试窗口不能写真实 interface.json，也不涉及窗口尺寸存储契约。
    monkeypatch.setattr(dialog, "_save_window_size", lambda: None)
    return dialog, system, manager


def test_reload_reads_external_changes_and_rebuilds_editor(editor):
    dialog, system, manager = editor
    registry = scene_registry.get_registry()
    old_tab = dialog._tabs["sample"]
    assert not old_tab.get_regions()
    dialog._img_cache[("phone", "sample", "", 0)] = object()
    dialog._script_text.setPlainText("未保存的脚本")
    manifest = system / "scenes.yaml"
    stamp = manifest.stat()
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("通用", "测试"), encoding="utf-8",
    )
    # 即使外部更新保留文件时间和大小，显式重载也不能命中旧 YAML 缓存。
    os.utime(manifest, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    (system / "scenes" / "sample.yaml").write_text(
        "key: sample\nname: 示例\nregions:\n"
        "  - key: new_button\n    name: 新按钮\n    type: func\n",
        encoding="utf-8",
    )
    (system / "scenes" / "extra.yaml").write_text(
        "key: extra\nname: 新场景\nregions: []\n", encoding="utf-8",
    )
    before = {path: path.read_bytes() for path in system.rglob("*") if path.is_file()}

    dialog._btn_reload_scenes.click()

    assert scene_registry.get_registry() is registry
    assert registry.get_groups() == [("general", "测试")]
    assert registry.get_scene("extra") is not None
    assert dialog._find_scene_tab("extra") is not None
    assert dialog._tabs["sample"] is not old_tab
    assert dialog._tabs["sample"]._scene_version_value.text() == "系统"
    assert dialog._tabs["sample"]._layout_version_value.text() == "系统"
    assert [region.key for region in dialog._tabs["sample"].get_regions()] == ["new_button"]
    assert dialog._img_cache.get(("phone", "sample", "", 0)) is None
    assert dialog._get_current_scene_key() == "sample"
    assert dialog._current_layout.key == "phone"
    assert manager.get_active_layout_key() == "main_layout"
    assert dialog._script_text.toPlainText() == "未保存的脚本"
    assert not dialog._dirty_scenes
    assert all(path.read_bytes() == data for path, data in before.items())


def test_reload_requires_user_to_handle_unsaved_edits(editor, monkeypatch):
    dialog, _system, _manager = editor
    old_tab = dialog._tabs["sample"]
    dialog._mark_scene_dirty("sample")
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args))
    dialog._btn_reload_scenes.click()
    assert dialog._tabs["sample"] is old_tab
    assert dialog._dirty_scenes == {"sample"}
    assert len(messages) == 1
    assert "手动保存或放弃" in messages[0][2]

    # 用户已自行处理改动后才允许刷新，不在提示里代替用户保存或放弃。
    dialog._mark_all_scenes_clean()
    dialog._btn_reload_scenes.click()
    assert dialog._tabs["sample"] is not old_tab
    assert not dialog._dirty_scenes
