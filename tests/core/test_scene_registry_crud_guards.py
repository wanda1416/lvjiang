"""场景注册表 CRUD 边界保护。"""

import pytest
import yaml

from lvjiang.core.config.resolver import ConfigResolver, SystemContentProtected
from lvjiang.core.scene_config import load_scene_manifest
from lvjiang.core.scene_definition import SceneRegistry
from lvjiang.core.scene_definition_models import PointDef, RegionDef
from tests.case_matrix import case_matrix


def _registry_with_groups(*keys: str) -> SceneRegistry:
    registry = SceneRegistry.__new__(SceneRegistry)
    registry._groups = {key: key for key in keys}
    registry._group_order = list(keys)
    registry._group_scenes = {key: [] for key in keys}
    registry._scenes = {}
    registry._order = []
    return registry


def test_cannot_delete_last_group():
    registry = _registry_with_groups("main")

    with pytest.raises(ValueError, match="至少需要保留一个"):
        registry.delete_group("main")


@case_matrix("key", ["BadKey", "1scene", "中文", "bad-key"])
def test_create_group_rejects_invalid_key(key):
    registry = _registry_with_groups("main")

    with pytest.raises(ValueError, match="key 必须"):
        registry.create_group(key, "名称")


def test_create_group_rejects_empty_name():
    registry = _registry_with_groups("main")

    with pytest.raises(ValueError, match="名称不能为空"):
        registry.create_group("valid_key", "  ")


def test_user_rename_system_scene_is_rejected_before_write(tmp_path):
    system = tmp_path / "system"
    local = tmp_path / "local"
    scenes = system / "scenes"
    scenes.mkdir(parents=True)
    (scenes / "factory.yaml").write_text(
        yaml.dump({"key": "factory", "name": "系统场景", "regions": []},
                  allow_unicode=True),
        encoding="utf-8",
    )
    resolver = ConfigResolver(
        system_dir=system, local_dir=local, dev_mode=False,
    )
    registry = SceneRegistry(resolver=resolver)

    with pytest.raises(SystemContentProtected):
        registry.rename_scene("factory", "renamed", "新名称")

    assert not (local / "scenes" / "renamed.yaml").exists()
    scene = registry.get_scene("factory")
    assert scene is not None
    assert scene.key == "factory"
    assert scene.name == "系统场景"


def test_rename_scene_survives_in_place_hot_reload(tmp_path):
    system = tmp_path / "system"
    local = tmp_path / "local"
    scenes = system / "scenes"
    scenes.mkdir(parents=True)
    documents = {
        "before": {"key": "before", "name": "前"},
        "old": {
            "key": "old",
            "name": "旧场景",
            "regions": [{
                "key": "shared",
                "name": "共享区域",
                "type": "attr",
                "is_text": True,
                "is_clickable": False,
            }],
        },
        "after": {"key": "after", "name": "后"},
        "owner": {
            "key": "owner",
            "name": "引用方",
            "references": [{"scene": "old", "entity": "shared"}],
        },
    }
    for key, document in documents.items():
        (scenes / f"{key}.yaml").write_text(
            yaml.dump(document, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    resolver = ConfigResolver(
        system_dir=system, local_dir=local, dev_mode=True)
    order = ["before", "old", "after", "owner"]
    groups = {"main": ["before", "old", "after"], "other": ["owner"]}
    names = {"main": "主分组", "other": "其他"}
    (system / "scenes.yaml").write_text(
        yaml.dump({
            "schema_version": 2,
            "scenes": {
                "main": {
                    "name": "主分组",
                    "items": ["before", "old", "after"],
                    "disabled": ["old"],
                },
                "other": {"name": "其他", "items": ["owner"]},
            },
        }, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    registry = SceneRegistry(
        resolver=resolver,
        scene_order=order,
        group_config=groups,
        group_names=names,
        disabled_scenes={"old"},
    )

    def reload_in_place(_rel_path: str) -> None:
        manifest = load_scene_manifest(resolver)
        refreshed = SceneRegistry(
            resolver=resolver,
            scene_order=manifest.order,
            group_config=manifest.groups,
            group_names=manifest.group_names,
            disabled_scenes=manifest.disabled,
        )
        registry.__dict__.clear()
        registry.__dict__.update(refreshed.__dict__)

    resolver.add_change_listener(reload_in_place)

    registry.rename_scene("old", "new", "新场景")

    assert not (scenes / "old.yaml").exists()
    assert (scenes / "new.yaml").exists()
    assert registry.get_scene("old") is None
    assert registry.get_scene("new").name == "新场景"
    assert registry.all_scene_keys() == ["before", "new", "after", "owner"]
    assert registry.get_group_scenes("main") == ["before", "after"]
    assert registry._group_scenes["main"] == ["before", "new", "after"]
    assert "new" in registry._disabled_scenes
    owner = registry.get_scene("owner")
    assert owner.references[0].scene == "new"
    persisted_owner = yaml.safe_load(
        (scenes / "owner.yaml").read_text(encoding="utf-8"))
    assert persisted_owner["references"][0]["scene"] == "new"

    # UI 紧接着保存分组配置，这次写入同样会热重载。
    registry.save_group_config()
    manifest = load_scene_manifest(resolver)
    assert manifest.groups["main"] == ["before", "new", "after"]
    assert manifest.disabled == {"new"}
    assert registry._group_scenes["main"] == ["before", "new", "after"]


def test_explicit_scene_version_is_written_only_when_saved(tmp_path):
    system = tmp_path / "system"
    local = tmp_path / "local"
    scenes = system / "scenes"
    scenes.mkdir(parents=True)
    path = scenes / "factory.yaml"
    path.write_text(
        yaml.dump(
            {"content_version": 2, "key": "factory", "name": "系统场景"},
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    resolver = ConfigResolver(
        system_dir=system, local_dir=local, dev_mode=True,
    )
    registry = SceneRegistry(resolver=resolver)

    # 链接点击只在 UI 中记录 pending；对话框真正保存时才调这个 API。
    assert yaml.safe_load(path.read_text(encoding="utf-8"))[
        "content_version"] == 2
    registry.save_scene_content_version("factory", 3)
    assert yaml.safe_load(path.read_text(encoding="utf-8"))[
        "content_version"] == 3


def test_region_transition_survives_registry_reload(tmp_path):
    system = tmp_path / "system"
    local = tmp_path / "local"
    scenes = system / "scenes"
    scenes.mkdir(parents=True)
    path = scenes / "factory.yaml"
    path.write_text(
        yaml.dump({
            "key": "factory", "name": "系统场景",
            "regions": [{
                "key": "open", "name": "打开", "type": "func",
                "is_text": False, "is_clickable": True,
            }],
        }, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    resolver = ConfigResolver(
        system_dir=system, local_dir=local, dev_mode=True)
    registry = SceneRegistry(resolver=resolver)

    registry.update_region_in_scene(
        "factory", "open",
        RegionDef(
            key="open", name="打开", type="func", is_text=False,
            is_clickable=True, to="/result"),
    )

    reloaded = SceneRegistry(resolver=resolver)
    assert reloaded.get_scene("factory").regions[0].to == "/result"


def test_non_clickable_entities_never_serialize_transitions(tmp_path):
    system = tmp_path / "system"
    local = tmp_path / "local"
    scenes = system / "scenes"
    scenes.mkdir(parents=True)
    path = scenes / "factory.yaml"
    path.write_text(
        yaml.dump({"key": "factory", "name": "系统场景"},
                  allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    registry = SceneRegistry(resolver=ConfigResolver(
        system_dir=system, local_dir=local, dev_mode=True))

    registry.add_region_to_scene(
        "factory", RegionDef(
            key="label", name="标签", is_clickable=False, to="other"))
    registry.add_point_to_scene(
        "factory", PointDef(
            key="marker", name="标记", is_clickable=False, to="other"))

    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "to" not in saved["regions"][0]
    assert "to" not in saved["points"][0]


def test_reorder_scene_entities_persists_visible_subset_in_original_slots(
    tmp_path,
):
    system = tmp_path / "system"
    local = tmp_path / "local"
    scenes = system / "scenes"
    scenes.mkdir(parents=True)
    path = scenes / "factory.yaml"
    path.write_text(
        yaml.dump({
            "key": "factory",
            "name": "系统场景",
            "regions": [
                {"key": "base_a", "name": "A", "type": "func"},
                {"key": "view_b", "name": "B", "type": "func"},
                {"key": "base_c", "name": "C", "type": "func"},
                {"key": "view_d", "name": "D", "type": "func"},
            ],
        }, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    registry = SceneRegistry(resolver=ConfigResolver(
        system_dir=system, local_dir=local, dev_mode=True))

    assert registry.reorder_scene_entities(
        "factory", "regions", ["view_d", "view_b"])
    assert [r.key for r in registry.get_scene("factory").regions] == [
        "base_a", "view_d", "base_c", "view_b",
    ]
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert [item["key"] for item in saved["regions"]] == [
        "base_a", "view_d", "base_c", "view_b",
    ]
    assert not registry.reorder_scene_entities(
        "factory", "regions", ["view_d", "view_b"])


def test_reorder_scene_entities_rejects_invalid_requests(tmp_path):
    system = tmp_path / "system"
    scenes = system / "scenes"
    scenes.mkdir(parents=True)
    (scenes / "factory.yaml").write_text(
        yaml.dump({
            "key": "factory", "name": "系统场景",
            "regions": [
                {"key": "a", "name": "A", "type": "func"},
                {"key": "b", "name": "B", "type": "func"},
            ],
        }, sort_keys=False),
        encoding="utf-8",
    )
    registry = SceneRegistry(resolver=ConfigResolver(
        system_dir=system, local_dir=tmp_path / "local", dev_mode=True))

    with pytest.raises(ValueError, match="不支持排序"):
        registry.reorder_scene_entities("factory", "arrows", ["b", "a"])
    with pytest.raises(ValueError, match="重复 key"):
        registry.reorder_scene_entities("factory", "regions", ["a", "a"])
    with pytest.raises(ValueError, match="未知 key"):
        registry.reorder_scene_entities("factory", "regions", ["a", "missing"])
