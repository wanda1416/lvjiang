"""场景编辑器分组删除流程回归测试。"""

from types import SimpleNamespace

from PyQt6.QtWidgets import QMessageBox

from lvjiang.ui.scene_editor import scene_ops
from lvjiang.ui.scene_editor.scene_ops import SceneOpsMixin


def test_delete_group_persists_and_reapplies_layout(monkeypatch):
    calls: list[object] = []

    class Registry:
        def delete_group(self, key):
            calls.append(("delete", key))

        def save_group_config(self):
            calls.append("save")

        def get_groups(self):
            return [("empty_first", "首个空分组"), ("main", "主分组")]

        def get_group_scenes(self, key):
            return [] if key == "empty_first" else ["home"]

    registry = Registry()
    monkeypatch.setattr(scene_ops, "get_group_name", lambda _key: "空分组")
    monkeypatch.setattr(scene_ops, "get_registry", lambda: registry)
    monkeypatch.setattr(
        scene_ops.QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        scene_ops,
        "reload_scene_registry",
        lambda: calls.append("reload"),
    )

    editor = SimpleNamespace(
        _confirm_structure_change=lambda _action: True,
        _rebuild_group_tabs=lambda: calls.append("rebuild"),
        _apply_layout_to_tabs=lambda: calls.append("apply"),
        _select_scene=lambda key: calls.append(("select", key)),
        _status_bar=SimpleNamespace(showMessage=lambda _message: None),
    )

    SceneOpsMixin._do_delete_group(editor, "empty")

    assert calls == [
        ("delete", "empty"),
        "save",
        "reload",
        "rebuild",
        "apply",
        ("select", "home"),
    ]


def test_move_scene_persists_rebuilds_and_reselects(monkeypatch):
    calls: list[object] = []

    class Registry:
        def move_scene_to_group(self, scene_key, group_key):
            calls.append(("move", scene_key, group_key))

        def save_group_config(self):
            calls.append("save")

    registry = Registry()
    monkeypatch.setattr(scene_ops, "get_registry", lambda: registry)
    monkeypatch.setattr(scene_ops, "get_group_name", lambda _key: "目标")
    monkeypatch.setattr(scene_ops, "get_scene_name", lambda _key: "首页")
    monkeypatch.setattr(scene_ops, "reload_scene_registry", lambda: calls.append("reload"))

    editor = SimpleNamespace(
        _confirm_structure_change=lambda _action: True,
        _rebuild_group_tabs=lambda: calls.append("rebuild"),
        _apply_layout_to_tabs=lambda: calls.append("apply"),
        _select_scene=lambda key: calls.append(("select", key)),
        _status_bar=SimpleNamespace(showMessage=lambda _message: None),
    )

    SceneOpsMixin._do_move_scene_group(editor, "home", "target")

    assert calls == [
        ("move", "home", "target"),
        "save",
        "reload",
        "rebuild",
        "apply",
        ("select", "home"),
    ]
