"""场景配置热重载必须保留注册表对象身份。"""

from types import SimpleNamespace

import lvjiang.core.scene.registry as scene_registry


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
            order=[], groups={}, group_names={}, disabled=set(),
            group_layers={}, scene_layers={}),
    )
    monkeypatch.setattr(
        scene_registry, "SceneRegistry", lambda **_kwargs: refreshed)
    monkeypatch.setattr(scene_registry, "_rebuild_scene_globals", lambda: None)

    scene_registry.reload_scene_registry()

    assert scene_registry.get_registry() is original
    assert original.marker == "fresh"


def test_rebuilding_globals_keeps_the_previous_content_until_the_new_one_is_ready(
    monkeypatch,
):
    """重建全局缓存不能先把旧内容清空。

    这些字典是场景列表、分组名、区域清单的唯一来源，平台线程随时在读。旧实现
    先 clear() 再一边算一边填，读到空分组表的那一刻 UI 上的场景会瞬间消失；
    新实现先把新内容算完再替换，``get_groups()`` 期间旧内容必须还在。
    """
    monkeypatch.setattr(
        scene_registry, "SCENE_GROUPS_META", {"general": "通用"})
    monkeypatch.setattr(
        scene_registry, "GROUP_SCENES", {"general": ["only_scene"]})
    monkeypatch.setattr(scene_registry, "GROUP_ORDER", ["general"])
    observed: dict[str, object] = {}

    class _Stub:
        def all_scenes(self):
            return {}

        def get_groups(self):
            observed["order"] = list(scene_registry.GROUP_ORDER)
            observed["scenes"] = dict(scene_registry.GROUP_SCENES)
            return [("general", "通用")]

        def get_group_scenes(self, group_key):
            return ["only_scene"]

    monkeypatch.setattr(scene_registry, "_registry", _Stub())

    scene_registry._rebuild_scene_globals()

    assert observed["order"] == ["general"]
    assert observed["scenes"] == {"general": ["only_scene"]}
    assert scene_registry.GROUP_ORDER == ["general"]
    assert scene_registry.GROUP_SCENES == {"general": ["only_scene"]}
