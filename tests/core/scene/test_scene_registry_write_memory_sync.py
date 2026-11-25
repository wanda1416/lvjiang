"""场景结构改动写盘后会同步热重载，内存收敛与迁移批次必须扛得住。

移动/删除场景都是「改盘 → 通知注册表重载」，而重载是**原位替换**同一个
``SceneRegistry`` 的属性，重载内部还会跑一次 ``prepare_content_split``（由
``_load_manifest`` 调起）。历史实现按「进入方法时的旧状态」做收尾：

- 换组：``self._group_scenes[旧组].remove(scene_key)`` →
  ``ValueError: list.remove(x): x not in list``，界面弹「移动失败」；带布局
  坐标时这次异常还会让坐标目录级联与 ``_meta.json`` 落盘一起被跳过，留下
  「场景已换组、坐标还在旧组」的撕裂状态，下次加载被判成迁错坐标而整份
  不可用；
- 删除：``del self._scenes[key]`` → ``KeyError``，界面弹「删除失败」。

现在换组的整段迁移（场景文件 + 坐标目录 + ``_meta.json``）在同一个变更批次
内完成，重载只看到终态。三个用例分别守住这些契约。
"""

from __future__ import annotations

import json
from typing import NamedTuple

import pytest

from lvjiang.core.config.resolver import get_root_resolver
from lvjiang.core.layout.storage import scene_layout_rel
from lvjiang.core.scene import registry as scene_registry
from lvjiang.core.scene.registry import get_registry, reload_scene_registry

MOVE_KEY = "hot_reload_move_probe"
COORD_MOVE_KEY = "hot_reload_coord_move_probe"
DELETE_KEY = "hot_reload_delete_probe"


class HotReload(NamedTuple):
    resolver: object
    #: 每次变更通知的路径批次；批次生效时一次换组只应产生一条。
    notifications: list[tuple[str, ...]]
    #: 每条通知落地后，迁移守卫拦下的场景快照。
    blocked_snapshots: list[set[str]]


@pytest.fixture
def hot_reload(monkeypatch):
    """把生产环境的热重载监听挂到隔离 resolver 上，并记录通知与拉黑结果。

    ``registry`` 模块在导入时就把它挂在当时的 root resolver 上，而隔离配置的
    resolver 是启动后换进去的；用例显式补挂，才真的走「写盘即重载」的路径。
    """
    resolver = get_root_resolver()
    notifications: list[tuple[str, ...]] = []
    blocked: list[set[str]] = []

    def listener(paths: tuple[str, ...]) -> None:
        notifications.append(tuple(paths))
        scene_registry._on_config_change(paths)
        blocked.append(set(getattr(resolver, "content_split_blocked_scenes", set())))

    monkeypatch.setattr(
        resolver, "_batch_listeners", [*resolver._batch_listeners, listener])
    return HotReload(resolver, notifications, blocked)


def _purge(key: str) -> None:
    """把探针场景（定义与坐标）从两层配置里清掉，避免污染后续用例。"""
    resolver = get_root_resolver()
    for root in (resolver.local_dir, resolver.system_dir):
        for path in (root / "scenes").glob(f"*/{key}.yaml"):
            path.unlink()
        for path in (root / "layouts").glob(f"*/*/{key}.json"):
            path.unlink()
    reload_scene_registry()


def _meta_group_of(hot: HotReload, key: str) -> list[str]:
    """落盘后的 ``scenes/_meta.json`` 里，包含该场景的分组 key。"""
    meta = hot.resolver.resolve_read("scenes/_meta.json")
    assert meta is not None, "换组必须把分组元数据一并落盘"
    doc = json.loads(meta.read_text(encoding="utf-8"))
    groups = doc.get("groups") or []
    return [g["key"] for g in groups if key in (g.get("scenes") or [])]


def test_move_scene_to_group_after_hot_reload(hot_reload):
    registry = get_registry()
    groups = [group_key for group_key, _name in registry.get_groups()]
    assert len(groups) >= 2, "隔离配置里应当至少有两个场景分组"
    source_group, target_group = groups[0], groups[1]

    try:
        registry.create_scene(MOVE_KEY, "热重载换组探针", source_group)
        assert registry.get_scene_group(MOVE_KEY) == source_group
        hot_reload.notifications.clear()

        registry.move_scene_to_group(MOVE_KEY, target_group)

        assert registry.get_scene_group(MOVE_KEY) == target_group
        assert MOVE_KEY not in registry.get_group_scenes(source_group)
        assert MOVE_KEY in registry.get_group_scenes(target_group)
        assert hot_reload.resolver.resolve_read(
            f"scenes/{target_group}/{MOVE_KEY}.yaml") is not None
        assert hot_reload.resolver.resolve_read(
            f"scenes/{source_group}/{MOVE_KEY}.yaml") is None
        assert _meta_group_of(hot_reload, MOVE_KEY) == [target_group]
        # 整段迁移只该产生一条通知：中途的撕裂态不能漏给监听器
        assert len(hot_reload.notifications) == 1
        assert not any(hot_reload.blocked_snapshots)
    finally:
        _purge(MOVE_KEY)


def test_move_scene_with_layout_coords_finishes_migration(hot_reload):
    """带布局坐标的场景换组必须一次做完：坐标跟着走、不被判成迁错坐标。"""
    registry = get_registry()
    groups = [group_key for group_key, _name in registry.get_groups()]
    assert len(groups) >= 2, "隔离配置里应当至少有两个场景分组"
    source_group, target_group = groups[0], groups[1]

    try:
        registry.create_scene(COORD_MOVE_KEY, "热重载换组探针（带坐标）", source_group)
        coords = {"content_version": 1, "regions": [
            {"key": "buy", "x_ratio": 0.8, "y_ratio": 0.8,
             "w_ratio": 0.1, "h_ratio": 0.1}]}
        hot_reload.resolver.write_entity(
            f"layouts/android/{source_group}/{COORD_MOVE_KEY}.json",
            json.dumps(coords, ensure_ascii=False, indent=2) + "\n")
        reload_scene_registry()
        hot_reload.notifications.clear()

        registry.move_scene_to_group(COORD_MOVE_KEY, target_group)

        # 撕裂态留下的「迁错坐标」判定必须从未出现，否则场景整份不可用
        assert not any(hot_reload.blocked_snapshots)
        assert not getattr(hot_reload.resolver, "content_split_blocked_scenes", set())
        assert registry.get_scene(COORD_MOVE_KEY) is not None
        assert registry.get_scene_group(COORD_MOVE_KEY) == target_group
        assert hot_reload.resolver.resolve_read(
            scene_layout_rel("android", COORD_MOVE_KEY)) is not None
        assert hot_reload.resolver.resolve_read(
            f"layouts/android/{source_group}/{COORD_MOVE_KEY}.json") is None
        assert _meta_group_of(hot_reload, COORD_MOVE_KEY) == [target_group]
        assert len(hot_reload.notifications) == 1
    finally:
        _purge(COORD_MOVE_KEY)


def test_delete_scene_after_hot_reload(hot_reload):
    registry = get_registry()
    group = registry.get_groups()[0][0]

    try:
        registry.create_scene(DELETE_KEY, "热重载删除探针", group)
        hot_reload.notifications.clear()

        registry.delete_scene(DELETE_KEY)

        assert registry.get_scene(DELETE_KEY) is None
        assert DELETE_KEY not in registry.all_scene_keys()
        assert registry.get_scene_group(DELETE_KEY) is None
        assert hot_reload.resolver.resolve_read(
            f"scenes/{group}/{DELETE_KEY}.yaml") is None
    finally:
        _purge(DELETE_KEY)


def test_move_local_only_scene_keeps_layout_coords_in_local(hot_reload):
    """开发模式下移动仅存于 local 的场景时，布局坐标必须留在 local，不得漏写进 system。"""
    registry = get_registry()
    resolver = hot_reload.resolver
    groups = [group_key for group_key, _name in registry.get_groups()]
    assert len(groups) >= 2
    source_group, target_group = groups[0], groups[1]
    key = "hot_reload_local_only_probe"

    try:
        scene_yaml = f"key: {key}\nname: 本地探针\n"
        resolver.write_entity(f"scenes/{source_group}/{key}.yaml", scene_yaml, layer="local")
        coords = {"content_version": 1, "regions": []}
        resolver.write_entity(
            f"layouts/android/{source_group}/{key}.json",
            json.dumps(coords, ensure_ascii=False, indent=2) + "\n",
            layer="local",
        )
        reload_scene_registry()
        hot_reload.notifications.clear()

        registry.move_scene_to_group(key, target_group)

        assert not any(hot_reload.blocked_snapshots)
        assert (resolver.local_dir / f"scenes/{target_group}/{key}.yaml").is_file()
        assert not (resolver.system_dir / f"scenes/{target_group}/{key}.yaml").exists()
        assert (resolver.local_dir / f"layouts/android/{target_group}/{key}.json").is_file()
        assert not (resolver.local_dir / f"layouts/android/{source_group}/{key}.json").exists()
        assert not (resolver.system_dir / f"layouts/android/{target_group}/{key}.json").exists()
    finally:
        _purge(key)
