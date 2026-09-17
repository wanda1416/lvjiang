"""地图定义与地图管理：创建、HUD 场景生成、POI 校验、底图与删除。"""

import pytest
import yaml

from lvjiang.core.config.resolver import ConfigResolver, SystemContentProtected
from lvjiang.core.maps import (
    HUD_SCENE_GROUP_KEY,
    HeadingDetector,
    MapDef,
    MapError,
    MapManager,
    MapPoi,
    MapUiBinding,
    hud_scene_key_for,
)
from lvjiang.core.scene_config import load_scene_manifest
from lvjiang.core.scene_definition import SceneRegistry


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def _env(tmp_path, *, dev_mode=True):
    system = tmp_path / "system"
    local = tmp_path / "local"
    _write(system / "scenes.yaml", {
        "schema_version": 2,
        "scenes": {"general": {"name": "通用", "items": []}},
    })
    resolver = ConfigResolver(system_dir=system, local_dir=local, dev_mode=dev_mode)
    manifest = load_scene_manifest(resolver)
    registry = SceneRegistry(
        resolver=resolver, scene_order=manifest.order,
        group_config=manifest.groups, group_names=manifest.group_names,
        disabled_scenes=manifest.disabled,
    )

    def reload_in_place(rel_path: str) -> None:
        if not rel_path.startswith("scenes/"):
            return
        current = load_scene_manifest(resolver)
        refreshed = SceneRegistry(
            resolver=resolver, scene_order=current.order,
            group_config=current.groups, group_names=current.group_names,
            disabled_scenes=current.disabled,
        )
        registry.__dict__.clear()
        registry.__dict__.update(refreshed.__dict__)

    resolver.add_change_listener(reload_in_place)
    return resolver, registry, MapManager(resolver=resolver, registry=registry)


def test_create_writes_definition_and_hud_scene(tmp_path):
    resolver, registry, manager = _env(tmp_path)

    map_def = manager.create("duchenxu", "渡尘墟")

    assert map_def.key == "duchenxu" and map_def.layer == "system"
    assert map_def.ui.scene == "map_duchenxu"
    assert manager.list_keys() == ["duchenxu"]
    saved = yaml.safe_load((tmp_path / "system/maps/duchenxu.yaml").read_text("utf-8"))
    assert saved["content_version"] == 1          # 开发模式新文件从 v1 起步
    assert saved["navigation"] == {"mode": "closed_loop"}

    scene = registry.get_scene(hud_scene_key_for("duchenxu"))
    assert scene is not None and scene.name == "渡尘墟地图"
    assert [v.key for v in scene.views] == ["base", "full"]
    assert {r.key: r.view for r in scene.regions} == {
        "minimap": "", "open_map": "", "full_map": "full", "close_map": "full"}
    assert [p.key for p in scene.points] == ["minimap_center"]
    assert registry.get_group_scenes(HUD_SCENE_GROUP_KEY) == ["map_duchenxu"]
    assert load_scene_manifest(resolver).groups[HUD_SCENE_GROUP_KEY] == ["map_duchenxu"]


def test_create_rejects_bad_key_and_duplicates(tmp_path):
    _, _, manager = _env(tmp_path)
    manager.create("a1", "A")
    with pytest.raises(MapError, match="已存在"):
        manager.create("a1", "again")
    with pytest.raises(MapError, match="key 必须"):
        manager.create("1abc", "x")
    with pytest.raises(MapError, match="名称不能为空"):
        manager.create("ok", "  ")
    with pytest.raises(MapError, match="导航模式"):
        manager.create("ok", "x", navigation_mode="teleport")
    with pytest.raises(MapError, match="名称已存在"):
        manager.create("a2", "A")


def test_save_and_reload_pois_and_ui(tmp_path):
    _, _, manager = _env(tmp_path)
    map_def = manager.create("juezhanglin", "觉障林", navigation_mode="pathfind")
    manager._ensure_hud_scene("map_shared", "共享")
    map_def.pois = [
        MapPoi(key="exit_a", x=0.71, y=0.23, kind="exit", name="东撤离点"),
        MapPoi(key="spawn_1", x=0.1, y=0.8, kind="spawn"),
    ]
    map_def.ui = MapUiBinding(scene="map_shared")
    map_def.north_up = False
    manager.save(map_def)

    loaded = manager.load("juezhanglin")
    assert loaded.navigation_mode == "pathfind"
    assert loaded.north_up is False
    assert [p.to_dict() for p in loaded.pois] == [
        {"key": "exit_a", "kind": "exit", "name": "东撤离点", "x": 0.71, "y": 0.23},
        {"key": "spawn_1", "kind": "spawn", "x": 0.1, "y": 0.8},
    ]
    assert loaded.ui.scene == "map_shared" and loaded.ui.minimap == "minimap"
    assert loaded.ui.full_map == "full_map"


def test_save_rejects_missing_hud_scene_or_wrong_entity_kind(tmp_path):
    _, _, manager = _env(tmp_path)
    map_def = manager.create("duchenxu", "渡尘墟")
    map_def.ui.scene = "missing"
    with pytest.raises(MapError, match="HUD 场景不存在"):
        manager.save(map_def)

    map_def.ui.scene = "map_duchenxu"
    map_def.ui.minimap_center = "minimap"
    with pytest.raises(MapError, match="minimap_center"):
        manager.save(map_def)

    map_def.ui.minimap_center = "minimap_center"
    map_def.ui.full_map = "minimap"
    with pytest.raises(MapError, match="full_map 必须属于 full 视图"):
        manager.save(map_def)


@pytest.mark.parametrize("pois, message", [
    ([{"key": "a", "x": 1.2, "y": 0.5}], "超出底图"),
    ([{"key": "a", "x": 0.1, "y": 0.5}, {"key": "a", "x": 0.2, "y": 0.5}], "重复"),
    ([{"key": "Bad-Key", "x": 0.1, "y": 0.5}], "POI key"),
])
def test_from_dict_validates_pois(pois, message):
    with pytest.raises(MapError, match=message):
        MapDef.from_dict({"key": "m", "name": "M", "pois": pois})


def test_base_image_import_and_delete(tmp_path):
    resolver, registry, manager = _env(tmp_path)
    map_def = manager.create("duchenxu", "渡尘墟")
    assert manager.base_image_path(map_def) is None

    manager.import_base_image(map_def, b"\x89PNG fake")

    image = manager.base_image_path(map_def)
    assert image is not None and image.read_bytes() == b"\x89PNG fake"
    assert image == tmp_path / "system/maps/duchenxu/base.png"

    manager.delete("duchenxu")

    assert manager.list_keys() == []
    assert not image.exists()
    assert registry.get_scene("map_duchenxu") is None


def test_user_mode_cannot_delete_system_map_but_can_copy_to_local(tmp_path):
    _, _, dev = _env(tmp_path)
    map_def = dev.create("duchenxu", "渡尘墟")
    dev.import_base_image(map_def, b"img")

    resolver = ConfigResolver(
        system_dir=tmp_path / "system", local_dir=tmp_path / "local", dev_mode=False)
    user = MapManager(resolver=resolver, registry=object())

    with pytest.raises(SystemContentProtected):
        user.delete("duchenxu")
    assert user.load("duchenxu").is_system

    copied = user.copy_to_local("duchenxu")

    assert copied.layer == "local"
    assert (tmp_path / "local/maps/duchenxu.yaml").exists()
    assert (tmp_path / "local/maps/duchenxu/base.png").read_bytes() == b"img"


def test_copy_to_local_writes_local_even_in_dev_mode(tmp_path):
    _, _, manager = _env(tmp_path)
    map_def = manager.create("duchenxu", "渡尘墟")
    manager.save_with_image(map_def, b"img")

    copied = manager.copy_to_local("duchenxu")

    assert copied.layer == "local"
    assert (tmp_path / "local/maps/duchenxu.yaml").is_file()
    assert (tmp_path / "local/maps/duchenxu/base.png").read_bytes() == b"img"


def test_heading_detector_round_trip_and_validation(tmp_path):
    _, _, manager = _env(tmp_path)
    map_def = manager.create("duchenxu", "渡尘墟")
    assert map_def.heading.hsv_lower == (18, 90, 120)

    map_def.heading = HeadingDetector(hsv_lower=(10, 50, 60), hsv_upper=(50, 255, 255),
                                      window_ratio=0.3, min_area=20)
    manager.save(map_def)

    loaded = manager.load("duchenxu")
    assert loaded.heading.hsv_lower == (10, 50, 60)
    assert loaded.heading.window_ratio == 0.3 and loaded.heading.min_area == 20

    with pytest.raises(MapError, match="hsv_lower"):
        MapDef.from_dict({"key": "m", "name": "M",
                          "detectors": {"player_heading": {"hsv_lower": [1, 2]}}})
    with pytest.raises(MapError, match="window_ratio"):
        MapDef.from_dict({"key": "m", "name": "M",
                          "detectors": {"player_heading": {"window_ratio": 3}}})
    with pytest.raises(MapError, match="H=0–180"):
        MapDef.from_dict({"key": "m", "name": "M",
                          "detectors": {"player_heading": {
                              "hsv_lower": [181, 2, 3]}}})
    with pytest.raises(MapError, match="未知朝向识别方法"):
        MapDef.from_dict({"key": "m", "name": "M",
                          "detectors": {"player_heading": {"method": "guess"}}})


def test_rejects_unsafe_base_image_path():
    with pytest.raises(MapError, match="固定为 base.png"):
        MapDef.from_dict({"key": "m", "name": "M", "base_image": "../../x.png"})


def test_base_image_digest_rejects_mismatched_asset(tmp_path):
    _, _, manager = _env(tmp_path)
    map_def = manager.create("duchenxu", "渡尘墟")
    manager.import_base_image(map_def, b"first")
    manager.save(map_def)
    image = manager.base_image_path(map_def)
    assert image is not None
    image.write_bytes(b"changed")

    assert manager.base_image_path(manager.load("duchenxu")) is None


def test_save_with_image_restores_previous_asset_when_definition_fails(
        tmp_path, monkeypatch):
    _, _, manager = _env(tmp_path)
    map_def = manager.create("duchenxu", "渡尘墟")
    manager.save_with_image(map_def, b"old")
    old_sha = map_def.base_image_sha256
    image = manager.base_image_path(map_def)
    assert image is not None

    monkeypatch.setattr(
        manager, "save",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    with pytest.raises(OSError, match="disk full"):
        manager.save_with_image(map_def, b"new")

    assert image.read_bytes() == b"old"
    assert map_def.base_image_sha256 == old_sha


def test_delete_keeps_generated_scene_when_another_map_reuses_it(tmp_path):
    _, registry, manager = _env(tmp_path)
    first = manager.create("first", "第一张")
    second = manager.create("second", "第二张")
    second.ui.scene = first.ui.scene
    second.ui.generated_scene = False
    manager.save(second)

    manager.delete("first")

    assert registry.get_scene("map_first") is not None


def test_create_rolls_back_generated_scene_when_map_save_fails(
        tmp_path, monkeypatch):
    _, registry, manager = _env(tmp_path)

    def fail_save(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(manager, "save", fail_save)

    with pytest.raises(OSError, match="disk full"):
        manager.create("duchenxu", "渡尘墟")

    assert registry.get_scene("map_duchenxu") is None
