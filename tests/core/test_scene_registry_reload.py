"""场景配置热重载必须保留注册表对象身份。"""

from types import SimpleNamespace

import lvjiang.core.scene_registry as scene_registry


class _RegistryStub:
    def __init__(self, marker):
        self.marker = marker


def test_reload_updates_registry_in_place(monkeypatch):
    original = _RegistryStub("old")
    refreshed = _RegistryStub("fresh")
    monkeypatch.setattr(scene_registry, "_registry", original)
    monkeypatch.setattr(
        scene_registry, "_load_manifest",
        lambda: SimpleNamespace(
            order=[], groups={}, group_names={}, disabled=set()),
    )
    monkeypatch.setattr(
        scene_registry, "SceneRegistry", lambda **_kwargs: refreshed)
    monkeypatch.setattr(scene_registry, "_rebuild_scene_globals", lambda: None)

    scene_registry.reload_scene_registry()

    assert scene_registry.get_registry() is original
    assert original.marker == "fresh"
