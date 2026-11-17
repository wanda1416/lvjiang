"""scenes.yaml / layouts.yaml 拆分迁移（content_split）的常驻契约。

保护三个真实回归面：

1. 两阶段顺序：场景 key 与分组 key 允许相同，若先建分组定义文件
   ``scenes/{key}.yaml`` 会把平铺场景文件整文件覆盖——迁移必须先移场景入组；
2. 用户侧 ``config/local`` 的旧内容（地图分组、坐标、layouts.yaml 覆盖）
   由运行时迁移自动转换，不丢数据；
3. 幂等：迁移成功后再启动是空操作。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from lvjiang.core.config.content_split import (
    ensure_content_split,
    migrate_layer,
    needs_migration,
)


def _write_yaml(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, allow_unicode=True,
                                   default_flow_style=False, sort_keys=False),
                    encoding="utf-8")


def _write_json(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def _build_legacy(root: Path) -> None:
    """复刻迁移前的 system 结构；map 场景与 map 分组撞名（隐藏巨坑）。"""
    _write_yaml(root / "scenes.yaml", {
        "schema_version": 2,
        "scenes": {
            "map": {"name": "地图", "items": ["map_common", "map"], "disabled": []},
            "general": {"name": "通用", "items": ["combat"]},
        },
    })
    # 场景 key == 分组 key：顶层平铺文件 scenes/map.yaml 是「场景」，
    # 而迁移要写的分组定义恰好也叫 scenes/map.yaml
    _write_yaml(root / "scenes" / "map.yaml",
                {"key": "map", "name": "同名场景"})
    _write_yaml(root / "scenes" / "map_common.yaml",
                {"key": "map_common", "name": "常用"})
    _write_yaml(root / "scenes" / "combat.yaml",
                {"key": "combat", "name": "战斗"})
    _write_yaml(root / "layouts.yaml", {
        "schema_version": 2,
        "layouts": {
            "android": {"name": "安卓布局", "env": "android",
                        "canvas": {"x_ratio": 0.0, "y_ratio": 0.0,
                                   "w_ratio": 1.0, "h_ratio": 1.0}},
            "cast": {"name": "投屏", "extends": "android",
                     "canvas": {"x_ratio": 0.0, "y_ratio": 0.05,
                                "w_ratio": 1.0, "h_ratio": 0.9}},
        },
    })
    _write_json(root / "layouts" / "android" / "map_common.json",
                {"regions": [{"key": "btn", "name": "按钮",
                              "x_ratio": 0.1, "y_ratio": 0.2,
                              "w_ratio": 0.3, "h_ratio": 0.4}]})


def _build_legacy_local(root: Path) -> None:
    """复刻迁移前的 local：自己的分组 + layouts.yaml 聚合覆盖（标定 canvas）。"""
    _write_yaml(root / "scenes.yaml", {
        "scenes": {"map": {"name": "地图", "items": ["map_local"]}},
    })
    _write_yaml(root / "scenes" / "map_local.yaml",
                {"key": "map_local", "name": "本地地图"})
    _write_yaml(root / "layouts.yaml", {
        "layouts": {"android": {
            "canvas": {"x_ratio": 0.01, "y_ratio": 0.02,
                       "w_ratio": 0.98, "h_ratio": 0.97}},
            "__deleted__": []},
    })
    _write_json(root / "layouts" / "android" / "map_local.json", {"regions": []})


def test_migration_moves_scenes_before_splitting_and_preserves_collision(tmp_path):
    """撞名场景必须先入组；分组定义写入时不得覆盖场景文件。"""
    system = tmp_path / "system"
    _build_legacy(system)
    assert migrate_layer(system) is True

    # 验收标志：聚合文件消失
    assert not (system / "scenes.yaml").exists()
    assert not (system / "layouts.yaml").exists()

    # 撞名场景完好：内容是场景定义，不是分组定义 {"name": ...}
    scene = yaml.safe_load(
        (system / "scenes" / "map" / "map.yaml").read_text(encoding="utf-8"))
    assert scene["key"] == "map" and scene["name"] == "同名场景"
    # 分组定义独立存在（同名不同路径层级）
    group = yaml.safe_load(
        (system / "scenes" / "map.yaml").read_text(encoding="utf-8"))
    assert group == {"name": "地图"}

    # 顺序、disabled 落盘形态
    meta = json.loads((system / "scenes" / "_meta.json").read_text(encoding="utf-8"))
    assert [g["key"] for g in meta["groups"]] == ["map", "general"]
    assert meta["groups"][0]["scenes"] == ["map_common", "map"]

    # 布局清单与坐标随分组移动
    assert (system / "layouts" / "android.yaml").is_file()
    assert (system / "layouts" / "cast.yaml").is_file()
    assert (system / "layouts" / "_meta.json").is_file()
    assert (system / "layouts" / "android" / "map" / "map_common.json").is_file()
    assert not (system / "layouts" / "android" / "map_common.json").exists()

    # 迁移后加载链路可用
    from lvjiang.core.config.resolver import ConfigResolver
    from lvjiang.core.layout.config import load_layout_entries
    from lvjiang.core.scene.config import load_scene_manifest
    resolver = ConfigResolver(system_dir=system, local_dir=tmp_path / "none",
                              dev_mode=False)
    manifest = load_scene_manifest(resolver)
    assert manifest.groups == {
        "map": ["map_common", "map"], "general": ["combat"]}
    assert manifest.group_names["map"] == "地图"
    entries = load_layout_entries(resolver)
    assert list(entries) == ["android", "cast"]
    assert entries["cast"].extends == "android"


def test_local_layer_migrates_user_content_and_canvas_patch(tmp_path):
    """用户侧 local：分组、场景、坐标、layouts.yaml 覆盖全部自动转换。"""
    system = tmp_path / "system"
    local = tmp_path / "local"
    _build_legacy(system)
    _build_legacy_local(local)
    assert ensure_content_split(system, local) is True

    assert not (local / "scenes.yaml").exists()
    assert not (local / "layouts.yaml").exists()
    assert (local / "scenes" / "map.yaml").is_file()
    assert (local / "scenes" / "map" / "map_local.yaml").is_file()
    assert (local / "layouts" / "android.yaml").is_file()
    assert (local / "layouts" / "android" / "map" / "map_local.json").is_file()

    # local layouts.yaml 里的 canvas 覆盖 → local layouts/android.yaml 字段补丁
    patch = yaml.safe_load(
        (local / "layouts" / "android.yaml").read_text(encoding="utf-8"))
    assert patch["canvas"] == {"x_ratio": 0.01, "y_ratio": 0.02,
                               "w_ratio": 0.98, "h_ratio": 0.97}

    # 合并视图：local 布局清单作为字段补丁叠加在 system 之上，name 不丢
    from lvjiang.core.config.resolver import ConfigResolver
    from lvjiang.core.layout.config import load_layout_entries
    resolver = ConfigResolver(system_dir=system, local_dir=local, dev_mode=False)
    entries = load_layout_entries(resolver)
    assert entries["android"].name == "安卓布局"
    assert entries["android"].canvas["w_ratio"] == pytest.approx(0.98)
    assert list(entries) == ["android", "cast"]


def test_migration_is_idempotent(tmp_path):
    system, local = tmp_path / "system", tmp_path / "local"
    _build_legacy(system)
    _build_legacy_local(local)
    assert ensure_content_split(system, local) is True
    assert needs_migration(system) is False
    assert needs_migration(local) is False
    # 再次启动：空操作，不动任何文件
    assert ensure_content_split(system, local) is False


def test_migration_refuses_to_overwrite_existing_target(tmp_path):
    """目标已存在即整体报错停止，绝不覆盖（保留原文件供人工处理）。"""
    system = tmp_path / "system"
    _build_legacy(system)
    # 预先放一个目标位置的同名文件（内容不同）
    target = system / "scenes" / "map" / "map.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("key: other\nname: 已有文件\n", encoding="utf-8")
    with pytest.raises(ValueError, match="拒绝覆盖"):
        migrate_layer(system)
    # 冲突文件未被覆盖
    assert yaml.safe_load(target.read_text(encoding="utf-8"))["key"] == "other"
