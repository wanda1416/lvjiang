"""模板图库：分组判定、索引双层合并、清单与改名/删除。

全部在 tmp_path 上用显式 resolver 构造，不触碰真实 config；引用关系（布局绑定、脚本按名）
用注入值替身，避免依赖当前仓库内容。
"""

from __future__ import annotations

import numpy as np
import pytest
import yaml

from lvjiang.core.config.resolver import ConfigResolver, SystemContentProtected
from lvjiang.core.templates import library


def _resolver(tmp_path, *, dev_mode: bool) -> ConfigResolver:
    return ConfigResolver(
        system_dir=tmp_path / "system", local_dir=tmp_path / "local",
        dev_mode=dev_mode, track_content=False, config_dir=tmp_path)


def _png_bytes(size: tuple[int, int] = (12, 10)) -> bytes:
    """结构化的小 PNG（随机噪点，避免被当成纯色模板拒绝）。"""
    import cv2
    image = np.random.default_rng(7).integers(
        0, 255, (size[1], size[0], 3), dtype=np.uint8)
    return cv2.imencode(".png", image)[1].tobytes()


def _write_png(cfg: ConfigResolver, name: str, *, layer: str = "system") -> None:
    root = cfg.system_dir if layer == "system" else cfg.local_dir
    path = root / "templates" / f"{name}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_png_bytes())


def _write_index(cfg: ConfigResolver, doc: dict, *, layer: str = "system") -> None:
    root = cfg.system_dir if layer == "system" else cfg.local_dir
    root.mkdir(parents=True, exist_ok=True)
    path = root / "templates" / "templates.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")


# ─── 分组判定 ────────────────────────────────────────────

def test_root_layout_groups_exclude_alias_layouts():
    """布局分组只看根布局；别名布局（android_cast 等）继承根目录，不能单独成组。"""
    assert library.root_layout_groups() == {"android", "desktop"}
    assert library.is_layout_group("android/game_main_page/ditu")
    assert not library.is_layout_group("common/tracked_person_avatar")


def test_group_of_takes_first_segment():
    assert library.group_of("android/bag_detail/back") == "android"
    assert library.group_of("common/locked") == "common"


# ─── 索引读写 ────────────────────────────────────────────

def test_set_entry_records_size_and_omits_default_role(tmp_path):
    cfg = _resolver(tmp_path, dev_mode=True)
    library.set_entry("common/x", record_w=2800, record_h=1260, resolver=cfg)

    entry = library.load_index(cfg)["common/x"]
    assert (entry["record_w"], entry["record_h"]) == (2800, 1260)
    assert "role" not in entry          # 默认角色不落盘


def test_set_entry_writes_sample_role_and_can_clear_size(tmp_path):
    cfg = _resolver(tmp_path, dev_mode=True)
    library.set_entry("common/locked", role=library.ROLE_SAMPLE, resolver=cfg)
    assert library.load_index(cfg)["common/locked"]["role"] == "sample"

    library.set_entry("common/x", record_w=100, record_h=50, resolver=cfg)
    library.set_entry("common/x", record_w=0, record_h=0, resolver=cfg)
    # 没有任何字段的条目等于"没登记"：整条移除，索引只保留有信息量的条目
    assert "common/x" not in library.load_index(cfg)

    with pytest.raises(ValueError):
        library.set_entry("common/x", record_w=100, record_h=0, resolver=cfg)
    with pytest.raises(ValueError):
        library.set_entry("common/x", role="bogus", resolver=cfg)


def test_user_mode_writes_only_its_own_diff(tmp_path):
    """用户模式只写自己的改动：系统条目原样保留，合并视图看到覆盖值。"""
    cfg = _resolver(tmp_path, dev_mode=False)
    _write_index(cfg, {"version": 1, "templates": {
        "common/a": {"record_w": 100, "record_h": 50},
        "common/b": {"record_w": 200, "record_h": 100},
    }})

    library.set_entry("common/a", note="改过", resolver=cfg)

    overlay = yaml.safe_load(
        (cfg.local_dir / "templates/templates.yaml").read_text(encoding="utf-8"))
    assert list(overlay["templates"]) == ["common/a"]
    assert overlay["templates"]["common/a"]["note"] == "改过"
    merged = library.load_index(cfg)
    assert merged["common/a"] == {"record_w": 100, "record_h": 50, "note": "改过"}
    assert merged["common/b"] == {"record_w": 200, "record_h": 100}
    # 系统文件没被改写
    system_doc = yaml.safe_load(
        (cfg.system_dir / "templates/templates.yaml").read_text(encoding="utf-8"))
    assert "note" not in system_doc["templates"]["common/a"]


def test_drop_entry_in_user_mode_writes_deleted(tmp_path):
    cfg = _resolver(tmp_path, dev_mode=False)
    _write_index(cfg, {"version": 1, "templates": {
        "common/a": {"record_w": 100, "record_h": 50}}})

    library.drop_entry("common/a", cfg)

    assert library.load_index(cfg) == {}
    overlay = yaml.safe_load(
        (cfg.local_dir / "templates/templates.yaml").read_text(encoding="utf-8"))
    assert overlay["templates"]["__deleted__"] == ["common/a"]


# ─── 清单 ────────────────────────────────────────────────

def test_templates_merges_index_and_references(tmp_path, monkeypatch):
    cfg = _resolver(tmp_path, dev_mode=False)
    _write_png(cfg, "android/demo/back")
    _write_png(cfg, "common/avatar")
    _write_png(cfg, "common/locked")
    _write_index(cfg, {"version": 1, "templates": {
        "common/avatar": {"record_w": 2800, "record_h": 1260},
        "common/locked": {"role": "sample"},
    }})
    monkeypatch.setattr(library, "bindings_by_name", lambda: {
        "android/demo/back": (library.TemplateBinding("android", "demo", "back"),)})
    monkeypatch.setattr(library, "script_refs", lambda: {
        "common/avatar": ("workflows/x.wf",)})

    items = {item.name: item for item in library.templates(resolver=cfg)}

    assert set(items) == {"android/demo/back", "common/avatar", "common/locked"}
    assert items["android/demo/back"].is_layout_group
    assert items["android/demo/back"].bindings[0].dsl() == "[demo].back"
    assert items["common/avatar"].has_record
    assert items["common/avatar"].scripts == ("workflows/x.wf",)
    assert items["common/locked"].is_sample
    assert not items["common/locked"].has_record
    # 样本默认不进清单
    listed = {item.name for item in library.templates(
        resolver=cfg, include_samples=False)}
    assert listed == {"android/demo/back", "common/avatar"}


# ─── 改名 / 删除 ─────────────────────────────────────────

def test_rename_moves_bytes_and_index_entry(tmp_path):
    cfg = _resolver(tmp_path, dev_mode=True)
    _write_png(cfg, "common/old")
    original = (cfg.system_dir / "templates/common/old.png").read_bytes()
    _write_index(cfg, {"version": 1, "templates": {
        "common/old": {"record_w": 640, "record_h": 360}}})

    library.rename_template("common/old", "common/new", resolver=cfg)

    assert not (cfg.system_dir / "templates/common/old.png").exists()
    assert (cfg.system_dir / "templates/common/new.png").read_bytes() == original
    index = library.load_index(cfg)
    assert "common/old" not in index
    assert index["common/new"] == {"record_w": 640, "record_h": 360}


def test_rename_allows_plain_groups_only(tmp_path):
    """普通分组之间可以改名/移动；布局分组整体只读（归场景管理）。"""
    cfg = _resolver(tmp_path, dev_mode=True)
    _write_png(cfg, "common/old")

    # 目标落在布局分组 → 拒绝
    with pytest.raises(ValueError, match="场景管理"):
        library.rename_template("common/old", "desktop/old", resolver=cfg)
    # 普通分组之间移动 → 允许
    library.rename_template("common/old", "misc/old", resolver=cfg)
    assert (cfg.system_dir / "templates/misc/old.png").is_file()
    assert not (cfg.system_dir / "templates/common/old.png").exists()
    with pytest.raises(FileNotFoundError):
        library.rename_template("common/ghost", "common/new", resolver=cfg)


def test_layout_group_templates_are_read_only(tmp_path):
    """布局分组的图由场景管理截取/删除；图库侧改名与删除都必须拒绝。"""
    cfg = _resolver(tmp_path, dev_mode=True)
    _write_png(cfg, "android/demo/back")

    assert library.managed_by_scene("android/demo/back")
    assert not library.managed_by_scene("common/locked")

    with pytest.raises(ValueError, match="场景管理"):
        library.rename_template("android/demo/back", "android/demo/back2",
                                resolver=cfg)
    with pytest.raises(ValueError, match="场景管理"):
        library.remove_template("android/demo/back", resolver=cfg)
    assert (cfg.system_dir / "templates/android/demo/back.png").is_file()


def test_rename_system_template_refused_in_user_mode(tmp_path):
    """系统模板在用户模式删不掉旧文件，改名必须整体拒绝而不是留下两份。"""
    cfg = _resolver(tmp_path, dev_mode=False)
    _write_png(cfg, "common/old")

    with pytest.raises(SystemContentProtected):
        library.rename_template("common/old", "common/new", resolver=cfg)
    assert (cfg.system_dir / "templates/common/old.png").is_file()
    assert not (cfg.local_dir / "templates/common/new.png").exists()


def test_remove_template_deletes_image_and_index_entry(tmp_path):
    cfg = _resolver(tmp_path, dev_mode=True)
    _write_png(cfg, "common/gone")
    _write_index(cfg, {"version": 1, "templates": {
        "common/gone": {"record_w": 100, "record_h": 100}}})

    assert library.remove_template("common/gone", resolver=cfg) is True
    assert not (cfg.system_dir / "templates/common/gone.png").exists()
    assert library.load_index(cfg) == {}
    assert library.remove_template("common/gone", resolver=cfg) is False
