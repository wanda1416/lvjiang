"""场景注册表 CRUD 边界保护。"""

import pytest
import yaml

from lvjiang.core.config.resolver import ConfigResolver, SystemContentProtected
from lvjiang.core.scene_definition import SceneRegistry


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


@pytest.mark.parametrize("key", ["BadKey", "1scene", "中文", "bad-key"])
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
        yaml.dump({"key": "factory", "name": "出厂场景", "regions": []},
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
    assert scene.name == "出厂场景"
