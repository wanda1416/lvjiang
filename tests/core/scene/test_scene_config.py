from __future__ import annotations

import json

import yaml

from lvjiang.core.config.resolver import ConfigResolver
from lvjiang.core.paths import get_config_system_dir
from lvjiang.core.scene.config import (
    build_scene_meta,
    load_scene_manifest,
)
from lvjiang.core.scene.definition import SceneRegistry


def _write_yaml(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(doc, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")


def _write_meta(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def _meta(groups: dict[str, list[str]]) -> dict:
    return {"groups": [{"key": k, "scenes": v} for k, v in groups.items()]}


def test_system_general_combat_is_prominent_single_view_scene(tmp_path):
    """通用分组按设置、战斗、移动、控制排列，战斗实体不再混入控制。"""
    resolver = ConfigResolver(
        system_dir=get_config_system_dir(),
        local_dir=tmp_path / "local",
        dev_mode=False,
    )
    manifest = load_scene_manifest(resolver)
    general = manifest.groups["general"]
    first = general.index("game_settings")
    assert general[first:first + 4] == [
        "game_settings", "general_combat", "general_move", "general_control",
    ]

    registry = SceneRegistry(resolver=resolver)
    combat = registry.get_scene("general_combat")
    control = registry.get_scene("general_control")
    assert combat is not None and combat.name == "战斗" and combat.views == []
    combat_keys = {
        *(region.key for region in combat.regions),
        *(point.key for point in combat.points),
    }
    assert {"weapon", "wuxueji", "qishu", "xuli", "bow"} <= combat_keys
    assert not combat_keys.intersection(
        {region.key for region in control.regions}
        | {point.key for point in control.points}
    )


def test_legacy_panel_calibration_fields_are_ignored_and_removed_on_save(tmp_path):
    system = tmp_path / "system"
    path = system / "scenes" / "general" / "main.yaml"
    _write_yaml(path, {
        "key": "main", "name": "Main",
        "panels": [{"key": "grid", "name": "Grid", "rows": 8, "cols": 9,
                    "min_visible": .76, "calibration": "image",
                    "scroll_direction": "both"}],
    })
    resolver = ConfigResolver(
        system_dir=system, local_dir=tmp_path / "local", dev_mode=True)
    registry = SceneRegistry(resolver=resolver)
    panel = registry.get_scene("main").panels[0]
    assert vars(panel) == {"key": "grid", "name": "Grid", "views": []}
    panel.name = "Renamed"
    registry.update_panel_in_scene("main", "grid", panel)
    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["panels"] == [{"key": "grid", "name": "Renamed"}]


def test_group_yaml_and_dir_are_distinct_namespaces(tmp_path):
    """scenes/{group}.yaml 是分组定义、scenes/{group}/ 是场景目录；两者同名共存合法。"""
    system = tmp_path / "system"
    _write_yaml(system / "scenes" / "map.yaml", {"name": "地图"})
    _write_yaml(system / "scenes" / "map" / "map_common.yaml",
                {"key": "map_common", "name": "常用"})
    _write_meta(system / "scenes" / "_meta.json", _meta({"map": ["map_common"]}))
    resolver = ConfigResolver(system_dir=system, local_dir=tmp_path / "local",
                              dev_mode=False)
    manifest = load_scene_manifest(resolver)
    assert manifest.groups == {"map": ["map_common"]}
    assert manifest.group_names == {"map": "地图"}
    assert manifest.order == ["map_common"]
    # 分组 key 与场景 key 允许同名（分属两个命名空间）
    _write_yaml(system / "scenes" / "map" / "map.yaml",
                {"key": "map", "name": "同名场景"})
    manifest = load_scene_manifest(resolver)
    # meta 已列 map_common；同名新场景（场景 key == 分组 key）稳定追加在后
    assert manifest.groups["map"] == ["map_common", "map"]


def test_meta_drift_degrades_stably_without_losing_entries(tmp_path):
    """目录是归属事实源：_meta.json 未列出的条目排后且不消失。"""
    system = tmp_path / "system"
    _write_yaml(system / "scenes" / "general.yaml", {"name": "通用"})
    for key in ("beta", "alpha"):
        _write_yaml(system / "scenes" / "general" / f"{key}.yaml",
                    {"key": key, "name": key})
    # meta 只列了 alpha，beta 未列；另有孤儿文件 gamma.yaml 完全未登记
    _write_meta(system / "scenes" / "_meta.json", _meta({"general": ["alpha"]}))
    _write_yaml(system / "scenes" / "general" / "gamma.yaml",
                {"key": "gamma", "name": "gamma"})
    resolver = ConfigResolver(system_dir=system, local_dir=tmp_path / "local",
                              dev_mode=False)
    manifest = load_scene_manifest(resolver)
    assert manifest.groups["general"] == ["alpha", "beta", "gamma"]
    assert manifest.order == ["alpha", "beta", "gamma"]


def test_local_meta_reorders_within_namespace_blocks_only(tmp_path):
    """system 恒在 local 区间之前；local meta 只能在各区间内部重排。"""
    system = tmp_path / "system"
    local = tmp_path / "local"
    _write_yaml(system / "scenes" / "general.yaml", {"name": "通用"})
    _write_yaml(system / "scenes" / "training.yaml", {"name": "培养"})
    for key in ("main",):
        _write_yaml(system / "scenes" / "general" / f"{key}.yaml",
                    {"key": key, "name": key})
    _write_yaml(system / "scenes" / "training" / "equip.yaml",
                {"key": "equip", "name": "equip"})
    _write_meta(system / "scenes" / "_meta.json",
                _meta({"general": ["main"], "training": ["equip"]}))
    # local 新建 map 分组 + local meta 把 training 排到 general 前（区间内重排）
    _write_yaml(local / "scenes" / "map.yaml", {"name": "地图"})
    _write_yaml(local / "scenes" / "map" / "mine.yaml",
                {"key": "mine", "name": "mine"})
    _write_meta(local / "scenes" / "_meta.json",
                _meta({"training": ["equip"], "general": ["main"], "map": ["mine"]}))
    resolver = ConfigResolver(system_dir=system, local_dir=local, dev_mode=False)
    manifest = load_scene_manifest(resolver)
    assert list(manifest.groups) == ["training", "general", "map"]
    assert manifest.scene_layers == {
        "main": "system", "equip": "system", "mine": "local"}


def test_disabled_scene_is_hidden_but_kept_in_order(tmp_path):
    system = tmp_path / "system"
    _write_yaml(system / "scenes" / "activity.yaml", {"name": "活动"})
    _write_yaml(system / "scenes" / "activity" / "active.yaml",
                {"key": "active", "name": "active"})
    _write_yaml(system / "scenes" / "activity" / "legacy.yaml",
                {"key": "legacy", "name": "legacy", "disabled": True})
    _write_meta(system / "scenes" / "_meta.json",
                _meta({"activity": ["active", "legacy"]}))
    resolver = ConfigResolver(system_dir=system, local_dir=tmp_path / "local",
                              dev_mode=False)
    manifest = load_scene_manifest(resolver)
    assert manifest.order == ["active", "legacy"]
    assert manifest.groups == {"activity": ["active", "legacy"]}
    registry = SceneRegistry(
        resolver=resolver,
        scene_order=manifest.order,
        group_config=manifest.groups,
        group_names=manifest.group_names,
        disabled_scenes=manifest.disabled,
    )
    # 定义仍加载、引用不失效，但分组 UI 不展示
    assert registry.get_scene("legacy") is not None
    assert registry.get_group_scenes("activity") == ["active"]


def test_user_save_writes_local_meta_patch_and_system_group_stays_first(tmp_path):
    system = tmp_path / "system"
    local = tmp_path / "local"
    _write_yaml(system / "scenes" / "general.yaml", {"name": "通用"})
    _write_yaml(system / "scenes" / "general" / "main.yaml",
                {"key": "main", "name": "main"})
    _write_yaml(system / "scenes" / "general" / "system_scene.yaml",
                {"key": "system_scene", "name": "system_scene"})
    _write_meta(system / "scenes" / "_meta.json",
                _meta({"general": ["main", "system_scene"]}))
    resolver = ConfigResolver(system_dir=system, local_dir=local, dev_mode=False)
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
    # mine 先以 local 场景身份进入分组（本地新建的真实形态：有文件）
    mine_path = local / "scenes" / "general" / "mine.yaml"
    mine_path.parent.mkdir(parents=True, exist_ok=True)
    mine_path.write_text(
        yaml.safe_dump({"key": "mine", "name": "mine"}, allow_unicode=True),
        encoding="utf-8")
    registry._group_scenes["general"].append("mine")
    registry._scene_layers["mine"] = "local"
    registry.reorder_scenes(["mine", "main", "system_scene"])
    registry.save_group_config()
    assert not (local / "scenes.yaml").exists()
    patch = json.loads((local / "scenes" / "_meta.json").read_text(encoding="utf-8"))
    assert patch["groups"][0]["scenes"] == ["main", "system_scene", "mine"]
    # system 后续新增仍进入用户合并视图（排在本区块已列条目之后）
    _write_yaml(system / "scenes" / "general" / "later.yaml",
                {"key": "later", "name": "later"})
    merged = load_scene_manifest(resolver)
    assert merged.groups["general"] == ["main", "system_scene", "later", "mine"]


def test_dev_save_keeps_local_only_group_and_scene_in_local(tmp_path):
    system = tmp_path / "system"
    local = tmp_path / "local"
    _write_yaml(system / "scenes" / "general.yaml", {"name": "通用"})
    _write_yaml(system / "scenes" / "general" / "main.yaml",
                {"key": "main", "name": "main"})
    _write_meta(system / "scenes" / "_meta.json", _meta({"general": ["main"]}))
    _write_yaml(local / "scenes" / "map.yaml", {"name": "地图"})
    _write_yaml(local / "scenes" / "map" / "map_one.yaml",
                {"key": "map_one", "name": "原名"})
    _write_meta(local / "scenes" / "_meta.json", _meta({"map": ["map_one"]}))
    resolver = ConfigResolver(system_dir=system, local_dir=local, dev_mode=True)
    manifest = load_scene_manifest(resolver)
    registry = SceneRegistry(
        resolver, manifest.order, manifest.groups, manifest.group_names,
        manifest.disabled,
        group_layers=manifest.group_layers, scene_layers=manifest.scene_layers)
    registry.rename_scene("map_one", "map_two", "新名")
    registry.save_group_config()

    assert not (system / "scenes" / "map" / "map_one.yaml").exists()
    assert not (system / "scenes" / "map" / "map_two.yaml").exists()
    assert not (local / "scenes" / "map" / "map_one.yaml").exists()
    assert yaml.safe_load(
        (local / "scenes" / "map" / "map_two.yaml").read_text(encoding="utf-8")
    )["name"] == "新名"
    assert not (system / "scenes" / "map.yaml").exists()
    assert load_scene_manifest(resolver).groups["map"] == ["map_two"]


def test_reorder_groups_clamps_local_after_system(tmp_path):
    system = tmp_path / "system"
    local = tmp_path / "local"
    _write_yaml(system / "scenes" / "general.yaml", {"name": "通用"})
    _write_yaml(system / "scenes" / "training.yaml", {"name": "培养"})
    _write_meta(system / "scenes" / "_meta.json",
                _meta({"general": [], "training": []}))
    _write_yaml(local / "scenes" / "map.yaml", {"name": "地图"})
    _write_meta(local / "scenes" / "_meta.json", _meta({"map": []}))
    resolver = ConfigResolver(system_dir=system, local_dir=local, dev_mode=False)
    manifest = load_scene_manifest(resolver)
    registry = SceneRegistry(
        resolver=resolver,
        group_config=manifest.groups,
        group_names=manifest.group_names,
        group_layers=manifest.group_layers,
    )
    # 拖动把 local 分组顶到最前：写入/内存态必须被钳回 system 区间之后
    registry.reorder_groups(["map", "training", "general"])
    assert registry._group_order == ["training", "general", "map"]


def test_build_scene_meta_is_canonical_shape():
    doc = build_scene_meta(["a", "b"], {"a": ["x"], "b": []})
    assert doc == {"groups": [{"key": "a", "scenes": ["x"]},
                              {"key": "b", "scenes": []}]}


def test_create_scene_survives_synchronous_registry_reload(tmp_path):
    """新场景文件通知先于 _meta.json 保存时，不得产生临时分组的重复归属。"""
    system = tmp_path / "system"
    local = tmp_path / "local"
    _write_yaml(system / "scenes" / "general.yaml", {"name": "通用"})
    _write_yaml(system / "scenes" / "target.yaml", {"name": "目标分组"})
    _write_meta(system / "scenes" / "_meta.json",
                _meta({"general": [], "target": []}))
    resolver = ConfigResolver(
        system_dir=system, local_dir=local, dev_mode=True)
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

    def reload_in_place(rel_path: str) -> None:
        if not rel_path.startswith("scenes/"):
            return
        current = load_scene_manifest(resolver)
        refreshed = SceneRegistry(
            resolver=resolver,
            scene_order=current.order,
            group_config=current.groups,
            group_names=current.group_names,
            disabled_scenes=current.disabled,
            group_layers=current.group_layers,
            scene_layers=current.scene_layers,
        )
        registry.__dict__.clear()
        registry.__dict__.update(refreshed.__dict__)

    resolver.add_change_listener(reload_in_place)
    registry.create_scene("new_scene", "新场景", group_key="target")

    assert registry.all_scene_keys().count("new_scene") == 1
    assert registry.get_group_scenes("general") == []
    assert registry.get_group_scenes("target") == ["new_scene"]

    registry.save_group_config()
    saved = load_scene_manifest(resolver)
    assert saved.groups == {
        "general": [],
        "target": ["new_scene"],
    }
