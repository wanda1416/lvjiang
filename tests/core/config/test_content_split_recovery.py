"""目录迁移保护原字节、有效跨层视图和可重复的中断恢复。"""

import base64
import json

import pytest

from lvjiang.core.config import content_split as split
from lvjiang.core.config.resolver import ConfigResolver
from lvjiang.core.scene.config import load_scene_manifest
from lvjiang.core.scene.definition import SceneRegistry
from tests.core.config.test_content_split import _build_legacy, _write_json, _write_yaml
from tests.core.config.test_content_split_overrides import system_tree


def test_flat_scene_override_without_aggregate_uses_system_group(tmp_path):
    system, local = tmp_path / "system", tmp_path / "local"
    system_tree(system)
    raw = b"key: activity_main\nname: Local\nregions: []\n"
    path = local / "scenes/activity_main.yaml"
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    split.ensure_content_split(system, local)
    assert (local / "scenes/activity/activity_main.yaml").read_bytes() == raw
    resolver = ConfigResolver(system_dir=system, local_dir=local, dev_mode=False)
    manifest = load_scene_manifest(resolver)
    reg = SceneRegistry(
        resolver=resolver,
        scene_order=manifest.order,
        group_config=manifest.groups,
        group_names=manifest.group_names,
    )
    assert reg.get_scene("activity_main").name == "Local"


def test_order_and_disabled_patch_without_local_entities(tmp_path):
    system, local = tmp_path / "system", tmp_path / "local"
    _build_legacy(system)
    split.migrate_layer(system)
    _write_yaml(
        local / "scenes.yaml",
        {
            "scenes": {
                "map": {
                    "items": {"__order__": ["map", "map_common"]},
                    "disabled": {"__added__": ["map_common"]},
                }
            }
        },
    )
    split.ensure_content_split(system, local)
    resolver = ConfigResolver(system_dir=system, local_dir=local, dev_mode=False)
    manifest = load_scene_manifest(resolver)
    assert manifest.groups["map"] == ["map", "map_common"]
    reg = SceneRegistry(
        resolver=resolver,
        scene_order=manifest.order,
        group_config=manifest.groups,
        group_names=manifest.group_names,
    )
    assert reg.get_scene("map_common").disabled
    assert not reg.get_scene("map").disabled


@pytest.mark.parametrize("fail_at", [2, 5, 10])
def test_interruption_resumes_fixed_plan_and_keeps_originals(
    tmp_path, monkeypatch, fail_at
):
    _build_legacy(tmp_path)
    original = (tmp_path / "scenes/map.yaml").read_bytes()
    commit = split._commit_write
    count = 0

    def interrupted(path, raw):
        nonlocal count
        count += 1
        if count == fail_at:
            raise OSError("injected interruption")
        commit(path, raw)

    with monkeypatch.context() as patch:
        patch.setattr(split, "_commit_write", interrupted)
        with pytest.raises(OSError, match="injected"):
            split.migrate_layer(tmp_path)
    split.migrate_layer(tmp_path)
    assert (tmp_path / "scenes/map/map.yaml").read_bytes() == original
    assert not (tmp_path / "scenes/map/general.yaml").exists()
    assert split.migrate_layer(tmp_path) is False
    record = json.loads(
        next((tmp_path / ".migrations/content-split-v2").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert base64.b64decode(record["originals"]["system/scenes/map.yaml"]) == original
    assert record["state"] == "complete"


def test_changed_file_after_interruption_is_not_overwritten(tmp_path, monkeypatch):
    _build_legacy(tmp_path)
    commit = split._commit_write

    def stop_after_record(path, raw):
        if path.suffix != ".json" or ".migrations" not in path.parts:
            raise OSError("stop")
        commit(path, raw)

    with monkeypatch.context() as patch:
        patch.setattr(split, "_commit_write", stop_after_record)
        with pytest.raises(OSError):
            split.migrate_layer(tmp_path)
    source = tmp_path / "scenes/map_common.yaml"
    source.write_bytes(b"key: map_common\nname: Edited\n")
    with pytest.raises(split.ContentSplitConflict, match="已变化"):
        split.migrate_layer(tmp_path)
    assert b"Edited" in source.read_bytes()


def test_ambiguous_old_bad_move_blocks_scene_without_changing_values(tmp_path):
    system, local = tmp_path / "system", tmp_path / "local"
    system_tree(system)
    old = local / "layouts/desktop/general/activity_main.json"
    _write_json(old, {"regions": [], "reference_positions": [{"x_ratio": 0}]})
    original = old.read_bytes()
    resolver = ConfigResolver(system_dir=system, local_dir=local, dev_mode=False)
    split.prepare_content_split(resolver)
    assert "activity_main" in resolver.content_split_blocked_scenes
    manifest = load_scene_manifest(resolver)
    reg = SceneRegistry(
        resolver=resolver,
        scene_order=manifest.order,
        group_config=manifest.groups,
        group_names=manifest.group_names,
    )
    assert reg.get_scene("activity_main") is None
    assert old.read_bytes() == original


def test_explicit_repair_preserves_original_and_refuses_different_target(tmp_path):
    system, local = tmp_path / "system", tmp_path / "local"
    system_tree(system)
    source = "layouts/desktop/general/activity_main.json"
    target = "layouts/desktop/activity/activity_main.json"
    _write_json(local / source, {"points": [{"key": "p", "x_ratio": 0.123}]})
    raw = (local / source).read_bytes()
    resolver = ConfigResolver(
        config_dir=tmp_path, system_dir=system, local_dir=local, dev_mode=False
    )
    _write_json(local / target, {"points": []})
    with pytest.raises(split.ContentSplitConflict, match="拒绝覆盖"):
        split.repair_misplaced_coordinate(resolver, source, target)
    assert (local / source).read_bytes() == raw
    (local / target).unlink()
    split.repair_misplaced_coordinate(resolver, source, target)
    assert (local / target).read_bytes() == raw
    split.prepare_content_split(resolver)
    assert not resolver.content_split_blocked_scenes
    record = json.loads(
        next((local / ".migrations/content-split-v2").glob("*.json")).read_text(
            encoding="utf-8"
        )
    )
    assert base64.b64decode(record["originals"][f"local/{source}"]) == raw


def test_two_legacy_layers_keep_disabled_overlay_out_of_system(tmp_path):
    import yaml
    system, local = tmp_path / "system", tmp_path / "local"
    _build_legacy(system)
    registry = yaml.safe_load((system / "scenes.yaml").read_text(encoding="utf-8"))
    registry["scenes"]["map"]["disabled"] = ["map_common"]
    _write_yaml(system / "scenes.yaml", registry)
    _write_yaml(local / "scenes.yaml", {"scenes": {"map": {
        "disabled": {"__removed__": ["map_common"]}}}})
    split.ensure_content_split(system, local)
    assert yaml.safe_load((system / "scenes/map/map_common.yaml").read_bytes())["disabled"] is True
    assert yaml.safe_load((local / "scenes/map/map_common.yaml").read_bytes())["disabled"] is False
    resolver = ConfigResolver(config_dir=tmp_path, system_dir=system, local_dir=local, dev_mode=False)
    manifest = load_scene_manifest(resolver)
    reg = SceneRegistry(resolver=resolver, scene_order=manifest.order,
                        group_config=manifest.groups, group_names=manifest.group_names)
    assert not reg.get_scene("map_common").disabled


def test_layout_full_metadata_partial_override_and_alias_use_migrated_coordinates(tmp_path, monkeypatch):
    from lvjiang.core.layout import storage
    from lvjiang.core.layout.config import load_layout_doc, load_layout_entries
    from lvjiang.core.scene import registry as registry_module

    system, local = tmp_path / "system", tmp_path / "local"
    system_tree(system)
    _write_yaml(system / "layouts/desktop.yaml", {
        "name": "Desktop", "env": "desktop", "desc": "官方说明", "aspect": "16:9",
        "canvas": {"x_ratio": 0, "y_ratio": 0, "w_ratio": 1, "h_ratio": 1}})
    _write_yaml(local / "layouts.yaml", {"layouts": {
        "desktop": {"desc": "", "canvas": {"x_ratio": 0.2}},
        "alias": {"name": "Alias", "extends": "desktop", "aspect": "4:3", "aspect_tolerance": 0.02,
                  "canvas": {"x_ratio": 0, "y_ratio": 0, "w_ratio": 0.8, "h_ratio": 1}}}})
    coords = {"regions": [{"key": "btn", "x_ratio": 0.75, "y_ratio": 0.2, "w_ratio": 0.15, "h_ratio": 0.4}]}
    _write_json(local / "layouts/desktop/activity_main.json", coords)
    raw = (local / "layouts/desktop/activity_main.json").read_bytes()
    split.ensure_content_split(system, local)
    resolver = ConfigResolver(config_dir=tmp_path, system_dir=system, local_dir=local, dev_mode=False)
    document = load_layout_doc(resolver)["layouts"]
    assert document["desktop"]["canvas"] == {"x_ratio": 0.2, "y_ratio": 0, "w_ratio": 1, "h_ratio": 1}
    assert document["desktop"]["desc"] == ""
    assert document["desktop"]["env"] == "desktop"
    assert document["alias"]["aspect"] == "4:3"
    assert document["alias"]["aspect_tolerance"] == 0.02
    assert load_layout_entries(resolver)["alias"].env == "desktop"
    manifest = load_scene_manifest(resolver)
    reg = SceneRegistry(resolver=resolver, scene_order=manifest.order,
                        group_config=manifest.groups, group_names=manifest.group_names)
    monkeypatch.setattr(storage, "get_resolver", lambda: resolver)
    monkeypatch.setattr(registry_module, "get_registry", lambda: reg)
    layout = storage.load_layout_by_key("alias")
    assert layout.regions["activity_main"][0].x_ratio == 0.75
    assert layout.canvas.w_ratio == 0.8
    assert (local / "layouts/desktop/activity/activity_main.json").read_bytes() == raw
