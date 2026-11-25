"""一层 ``scenes/_meta.json`` 只能写这一层归属的分组与场景。

注册表内存里是 system ∪ local 的合并视图。旧实现把这份合并视图整份交给
``resolver.write_entity("scenes/_meta.json")``，写哪层由运行模式决定：开发
模式写 system，于是 local 私有的分组（例如「地图」）被固化进随包的 system，
本机私有分层就变成了发布内容。
"""

from __future__ import annotations

import json

from lvjiang.core.config.resolver import ConfigResolver
from lvjiang.core.scene.config import load_scene_manifest
from lvjiang.core.scene.definition import SceneRegistry


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_registry(tmp_path) -> tuple[ConfigResolver, SceneRegistry]:
    system, local = tmp_path / "system", tmp_path / "local"
    _write(system / "scenes" / "general.yaml", "name: 通用\n")
    _write(system / "scenes" / "general" / "a.yaml", "key: a\nname: A\n")
    _write(local / "scenes" / "map.yaml", "name: 地图\n")
    _write(local / "scenes" / "map" / "m.yaml", "key: m\nname: M\n")
    _write(local / "scenes" / "_meta.json",
           json.dumps({"groups": [{"key": "map", "scenes": ["m"]}]},
                      ensure_ascii=False, indent=2) + "\n")

    resolver = ConfigResolver(
        system_dir=system, local_dir=local, dev_mode=True,
        config_dir=tmp_path, track_content=False)
    manifest = load_scene_manifest(resolver)
    registry = SceneRegistry(
        resolver=resolver,
        scene_order=manifest.order,
        group_config=manifest.groups,
        group_names=manifest.group_names,
        disabled_scenes=manifest.disabled,
        group_layers=manifest.group_layers,
        scene_layers=manifest.scene_layers,
    )
    return resolver, registry


def _meta_of(root) -> dict[str, list[str]]:
    doc = json.loads((root / "scenes" / "_meta.json").read_text(encoding="utf-8"))
    return {entry["key"]: entry["scenes"] for entry in doc["groups"]}


def test_save_group_config_keeps_layers_apart(tmp_path):
    resolver, registry = _build_registry(tmp_path)

    registry.save_group_config()

    system_meta = _meta_of(resolver.system_dir)
    local_meta = _meta_of(resolver.local_dir)
    assert system_meta == {"general": ["a"]}, "local 的「地图」分组不得写进 system"
    assert local_meta == {"map": ["m"]}, "local 排序补丁只保留自己归属的分组"


def test_local_scene_inside_system_group_is_ordered_in_local_meta(tmp_path):
    """用户在 system 分组里有同名 local 影子场景时，顺序只能写进 local。"""
    resolver, registry = _build_registry(tmp_path)
    _write(resolver.local_dir / "scenes" / "general" / "a.yaml",
           "key: a\nname: A（本地覆盖）\n")
    manifest = load_scene_manifest(resolver)
    registry = SceneRegistry(
        resolver=resolver,
        scene_order=manifest.order,
        group_config=manifest.groups,
        group_names=manifest.group_names,
        disabled_scenes=manifest.disabled,
        group_layers=manifest.group_layers,
        scene_layers=manifest.scene_layers,
    )

    registry.save_group_config()

    system_meta = _meta_of(resolver.system_dir)
    local_meta = _meta_of(resolver.local_dir)
    assert system_meta["general"] == ["a"]
    assert "map" not in system_meta
    assert local_meta["general"] == ["a"], "local 影子的组内顺序要落在 local meta"
    assert local_meta["map"] == ["m"]


def test_rename_system_group_with_local_shadow_writes_system_yaml(tmp_path):
    """开发模式下 system 分组在 local 有同名子目录时，重命名仍写回 system。"""
    resolver, registry = _build_registry(tmp_path)
    _write(resolver.local_dir / "scenes" / "general" / "a.yaml",
           "key: a\nname: A（本地覆盖）\n")
    manifest = load_scene_manifest(resolver)
    registry = SceneRegistry(
        resolver=resolver,
        scene_order=manifest.order,
        group_config=manifest.groups,
        group_names=manifest.group_names,
        disabled_scenes=manifest.disabled,
        group_layers=manifest.group_layers,
        scene_layers=manifest.scene_layers,
    )

    registry.rename_group("general", "通用改名")
    registry.save_group_config()

    assert "通用改名" in (resolver.system_dir / "scenes" / "general.yaml").read_text(encoding="utf-8")
    assert not (resolver.local_dir / "scenes" / "general.yaml").exists()
