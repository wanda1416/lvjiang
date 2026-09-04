"""场景注册表 CRUD 边界保护。"""

import pytest
import yaml

from lvjiang.core.config.resolver import ConfigResolver, SystemContentProtected
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
