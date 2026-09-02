from __future__ import annotations

import pytest
import yaml

from lvjiang.core.config.resolver import ConfigResolver
from lvjiang.core.scene_config import (
    build_scene_doc,
    load_scene_doc,
    load_scene_manifest,
    normalize_scene_doc,
    save_scene_doc,
)
from lvjiang.core.scene_definition import SceneRegistry


def _write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(doc, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")


def test_missing_version_is_v1_and_normalizes_to_v2():
    old = {
        "layout_scenes": {"general": ["main", "legacy"]},
        "group_names": {"general": "通用"},
    }
    assert normalize_scene_doc(old) == {
        "schema_version": 2,
        "scenes": {
            "general": {"name": "通用", "items": ["main", "legacy"]},
        },
    }


def test_explicit_v1_local_registry_delta_is_migrated():
    old = {
        "schema_version": 1,
        "layout_scenes": {
            "general": {
                "__added__": ["mine"],
                "__removed__": ["old"],
                "__order__": ["main", "mine"],
            },
        },
        "group_names": {"general": "我的通用"},
    }
    assert normalize_scene_doc(old) == {
        "schema_version": 2,
        "scenes": {
            "general": {
                "name": "我的通用",
                "items": old["layout_scenes"]["general"],
            },
        },
    }


def test_v2_disabled_scene_is_hidden_but_kept_in_order(tmp_path):
    system = tmp_path / "system"
    local = tmp_path / "local"
    _write(system / "scenes.yaml", {
        "schema_version": 2,
        "scenes": {
            "activity": {
                "name": "活动",
                "items": ["active", "legacy"],
                "disabled": ["legacy"],
            },
        },
    })
    manifest = load_scene_manifest(ConfigResolver(
        system_dir=system, local_dir=local, dev_mode=False))
    assert manifest.order == ["active", "legacy"]
    assert manifest.groups == {"activity": ["active", "legacy"]}
    assert manifest.disabled == {"legacy"}


def test_disabled_scene_remains_resolvable_but_is_absent_from_group_ui(tmp_path):
    system = tmp_path / "system"
    local = tmp_path / "local"
    for key in ("active", "legacy"):
        _write(system / "scenes" / f"{key}.yaml", {
            "key": key, "name": key, "regions": []})
    resolver = ConfigResolver(system_dir=system, local_dir=local, dev_mode=False)
    registry = SceneRegistry(
        resolver=resolver,
        scene_order=["active", "legacy"],
        group_config={"activity": ["active", "legacy"]},
        group_names={"activity": "活动"},
        disabled_scenes={"legacy"},
    )
    assert registry.get_scene("legacy") is not None
    assert registry.all_scene_keys() == ["active", "legacy"]
    assert registry.get_group_scenes("activity") == ["active"]
    assert registry.get_scene_group("legacy") == "activity"


def test_v2_disabled_must_reference_registered_item():
    with pytest.raises(ValueError, match="disabled 含未登记场景"):
        load = {
            "schema_version": 2,
            "scenes": {
                "activity": {
                    "name": "活动", "items": ["active"],
                    "disabled": ["missing"],
                },
            },
        }
        from lvjiang.core.scene_config import parse_scene_manifest
        parse_scene_manifest(load)


def test_v2_system_merges_old_v1_user_overlay(tmp_path):
    system = tmp_path / "system"
    local = tmp_path / "local"
    _write(system / "scenes.yaml", {
        "schema_version": 2,
        "scenes": {
            "general": {
                "name": "通用",
                "items": ["main", "old", "new_from_system"],
            },
        },
    })
    _write(local / "scenes.yaml", {
        "layout_scenes": {
            "general": {
                "__added__": ["mine"],
                "__removed__": ["old"],
                "__order__": ["main", "mine"],
            },
        },
        "group_names": {"general": "自定义通用"},
    })
    resolver = ConfigResolver(system_dir=system, local_dir=local, dev_mode=False)
    merged = load_scene_doc(resolver)
    assert merged["scenes"]["general"] == {
        "name": "自定义通用",
        "items": ["main", "mine", "new_from_system"],
    }


def test_user_save_uses_normalized_v2_base_and_does_not_freeze_registry(tmp_path):
    system = tmp_path / "system"
    local = tmp_path / "local"
    # 仓库/旧安装仍是隐含 v1，保存不得把完整 system 清单复制到 local。
    _write(system / "scenes.yaml", {
        "layout_scenes": {"general": ["main", "system_scene"]},
        "group_names": {"general": "通用"},
    })
    resolver = ConfigResolver(system_dir=system, local_dir=local, dev_mode=False)
    desired = build_scene_doc(
        ["general"], {"general": ["main", "system_scene", "mine"]},
        {"general": "通用"}, set())
    save_scene_doc(resolver, desired)
    saved = yaml.safe_load((local / "scenes.yaml").read_text(encoding="utf-8"))
    assert saved == {
        "scenes": {"general": {"items": {"__added__": ["mine"]}}},
    }
    # system 后续新增内容仍会进入用户合并视图。
    _write(system / "scenes.yaml", {
        "schema_version": 2,
        "scenes": {"general": {
            "name": "通用",
            "items": ["main", "system_scene", "later",],
        }},
    })
    assert load_scene_manifest(resolver).order == [
        "main", "system_scene", "later", "mine"]


def test_dev_save_converts_v1_file_to_v2(tmp_path):
    system = tmp_path / "system"
    local = tmp_path / "local"
    _write(system / "scenes.yaml", {
        "layout_scenes": {"general": ["main"]},
        "group_names": {"general": "通用"},
    })
    resolver = ConfigResolver(system_dir=system, local_dir=local, dev_mode=True)
    save_scene_doc(resolver, build_scene_doc(
        ["general"], {"general": ["main"]}, {"general": "通用"}, set()))
    saved = yaml.safe_load((system / "scenes.yaml").read_text(encoding="utf-8"))
    assert saved == {
        "schema_version": 2,
        "scenes": {"general": {"name": "通用", "items": ["main"]}},
    }


def test_future_schema_is_rejected():
    with pytest.raises(ValueError, match="当前只支持到 2"):
        normalize_scene_doc({"schema_version": 3, "scenes": {}})
