"""ReferenceDatabase 双层覆盖单测：条目级合并 / schema 整替换 / 用户模式写路由

全部在 tmp_path 上用显式 override 构造，不触碰真实 config 目录。
"""

import numpy as np
import pytest
import yaml

from lvjiang.core.reference_db import (
    DEFAULT_MATCH_THRESHOLD,
    MetaFieldDef,
    ReferenceDatabase,
)


def _write_yaml(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(doc, allow_unicode=True), encoding="utf-8")


def _entry(file, label, **meta):
    return {"file": file, "label": label, "meta": meta, "source": "", "notes": ""}


@pytest.fixture
def layers(tmp_path):
    """system/local 双层根目录与 yaml 路径"""
    system_dir = tmp_path / "system" / "references"
    local_dir = tmp_path / "local" / "references"
    system_yaml = tmp_path / "system" / "references.yaml"
    local_yaml = tmp_path / "local" / "references.yaml"
    return system_dir, system_yaml, local_dir, local_yaml


def _make_db(layers, dev_mode: bool) -> ReferenceDatabase:
    system_dir, system_yaml, local_dir, local_yaml = layers
    return ReferenceDatabase(
        system_dir=system_dir, system_yaml=system_yaml,
        local_dir=local_dir, local_yaml=local_yaml,
        dev_mode=dev_mode,
    )


# ─── 合并视图 ────────────────────────────────────────────

class TestMergedView:
    def test_union_of_layers(self, layers):
        _, system_yaml, _, local_yaml = layers
        _write_yaml(system_yaml, {"version": 1, "references": [
            _entry("g/A.png", "甲"), _entry("g/B.png", "乙")]})
        _write_yaml(local_yaml, {"version": 1, "references": [
            _entry("g/C.png", "丙")], "deleted": []})
        db = _make_db(layers, dev_mode=False)
        assert [e.file for e in db.entries] == ["g/A.png", "g/B.png", "g/C.png"]

    def test_local_entry_replaces_same_file(self, layers):
        _, system_yaml, _, local_yaml = layers
        _write_yaml(system_yaml, {"version": 1, "references": [
            _entry("g/A.png", "旧名", level=1)]})
        _write_yaml(local_yaml, {"version": 1, "references": [
            _entry("g/A.png", "新名", level=99)], "deleted": []})
        db = _make_db(layers, dev_mode=False)
        assert len(db.entries) == 1
        assert db.entries[0].label == "新名"
        assert db.entries[0].level == 99

    def test_deleted_removes_system_entry(self, layers):
        _, system_yaml, _, local_yaml = layers
        _write_yaml(system_yaml, {"version": 1, "references": [
            _entry("g/A.png", "甲"), _entry("g/B.png", "乙")]})
        _write_yaml(local_yaml, {"version": 1, "references": [],
                                 "deleted": ["g/A.png"]})
        db = _make_db(layers, dev_mode=False)
        assert [e.file for e in db.entries] == ["g/B.png"]

    def test_local_schema_replaces_whole_list(self, layers):
        _, system_yaml, _, local_yaml = layers
        _write_yaml(system_yaml, {"version": 1, "references": [], "meta_schema": [
            {"key": "level", "name": "等级"}, {"key": "grade", "name": "品阶"}]})
        _write_yaml(local_yaml, {"version": 1, "references": [], "deleted": [],
                                 "meta_schema": [{"key": "color", "name": "颜色"}]})
        db = _make_db(layers, dev_mode=False)
        assert [f.key for f in db.get_meta_schema()] == ["color"]

    def test_no_local_schema_uses_system(self, layers):
        _, system_yaml, _, local_yaml = layers
        _write_yaml(system_yaml, {"version": 1, "references": [], "meta_schema": [
            {"key": "level", "name": "等级"}]})
        _write_yaml(local_yaml, {"version": 1, "references": [], "deleted": []})
        db = _make_db(layers, dev_mode=False)
        assert [f.key for f in db.get_meta_schema()] == ["level"]


# ─── image_path 解析 ─────────────────────────────────────

class TestImagePath:
    def test_local_wins_when_exists(self, layers):
        system_dir, _, local_dir, _ = layers
        (system_dir / "g").mkdir(parents=True)
        (system_dir / "g" / "A.png").write_bytes(b"sys")
        (local_dir / "g").mkdir(parents=True)
        (local_dir / "g" / "A.png").write_bytes(b"loc")
        db = _make_db(layers, dev_mode=False)
        assert db.image_path("g/A.png") == local_dir / "g" / "A.png"

    def test_falls_back_to_system(self, layers):
        system_dir, _, _, _ = layers
        db = _make_db(layers, dev_mode=False)
        assert db.image_path("g/A.png") == system_dir / "g" / "A.png"


# ─── 用户模式写路由 ──────────────────────────────────────

class TestUserModeWrites:
    def test_add_entry_image_lands_in_local(self, layers):
        _, system_yaml, local_dir, local_yaml = layers
        _write_yaml(system_yaml, {"version": 1, "references": []})
        db = _make_db(layers, dev_mode=False)
        img = np.zeros((2, 2, 3), dtype=np.uint8)
        entry = db.add_entry(label="定音石", meta={"group": "调律材料"},
                             image_data=img)
        assert (local_dir / entry.file).exists()
        overlay = yaml.safe_load(local_yaml.read_text(encoding="utf-8"))
        assert [e["file"] for e in overlay["references"]] == [entry.file]
        # 合并视图可见
        assert db.get_entry(entry.file) is not None

    def test_remove_system_entry_appends_deleted(self, layers):
        system_dir, system_yaml, _, local_yaml = layers
        (system_dir / "g").mkdir(parents=True)
        (system_dir / "g" / "A.png").write_bytes(b"sys")
        _write_yaml(system_yaml, {"version": 1, "references": [
            _entry("g/A.png", "甲")]})
        db = _make_db(layers, dev_mode=False)
        assert db.remove_entry("g/A.png") is True
        # system 图片与 yaml 均不动
        assert (system_dir / "g" / "A.png").exists()
        assert yaml.safe_load(system_yaml.read_text(
            encoding="utf-8"))["references"]
        overlay = yaml.safe_load(local_yaml.read_text(encoding="utf-8"))
        assert overlay["deleted"] == ["g/A.png"]
        assert db.entries == []

    def test_remove_local_only_entry_deletes_file(self, layers):
        _, system_yaml, local_dir, local_yaml = layers
        _write_yaml(system_yaml, {"version": 1, "references": []})
        (local_dir / "g").mkdir(parents=True)
        (local_dir / "g" / "B.png").write_bytes(b"loc")
        _write_yaml(local_yaml, {"version": 1, "references": [
            _entry("g/B.png", "乙")], "deleted": []})
        db = _make_db(layers, dev_mode=False)
        assert db.remove_entry("g/B.png") is True
        assert not (local_dir / "g" / "B.png").exists()
        # overlay 无内容 → 覆盖文件被删
        assert not local_yaml.exists()

    def test_update_system_entry_copies_to_local_shadow(self, layers):
        system_dir, system_yaml, _, local_yaml = layers
        (system_dir / "g").mkdir(parents=True)
        (system_dir / "g" / "A.png").write_bytes(b"sys")
        _write_yaml(system_yaml, {"version": 1, "references": [
            _entry("g/A.png", "旧名")]})
        db = _make_db(layers, dev_mode=False)
        assert db.update_entry("g/A.png", label="新名") is True
        overlay = yaml.safe_load(local_yaml.read_text(encoding="utf-8"))
        assert overlay["references"][0]["file"] == "g/A.png"  # file 不变
        assert overlay["references"][0]["label"] == "新名"
        # system yaml 原条目不动
        sys_doc = yaml.safe_load(system_yaml.read_text(encoding="utf-8"))
        assert sys_doc["references"][0]["label"] == "旧名"
        # 合并视图取影子
        assert db.get_entry("g/A.png").label == "新名"

    def test_set_meta_schema_writes_local(self, layers):
        _, system_yaml, _, local_yaml = layers
        _write_yaml(system_yaml, {"version": 1, "references": [], "meta_schema": [
            {"key": "level", "name": "等级"}]})
        db = _make_db(layers, dev_mode=False)
        db.set_meta_schema([MetaFieldDef(key="color", name="颜色")])
        overlay = yaml.safe_load(local_yaml.read_text(encoding="utf-8"))
        assert [f["key"] for f in overlay["meta_schema"]] == ["color"]
        # system 不动
        sys_doc = yaml.safe_load(system_yaml.read_text(encoding="utf-8"))
        assert [f["key"] for f in sys_doc["meta_schema"]] == ["level"]


# ─── 匹配度阈值 ────────────────────────────────────

class TestMatchThreshold:
    def test_default_when_absent(self, layers):
        _, system_yaml, _, _ = layers
        _write_yaml(system_yaml, {"version": 1, "references": []})
        db = _make_db(layers, dev_mode=False)
        assert db.get_match_threshold() == DEFAULT_MATCH_THRESHOLD

    def test_local_overrides_system(self, layers):
        _, system_yaml, _, local_yaml = layers
        _write_yaml(system_yaml, {"version": 1, "references": [],
                                  "match_threshold": 0.1})
        _write_yaml(local_yaml, {"version": 1, "references": [], "deleted": [],
                                 "match_threshold": 0.3})
        db = _make_db(layers, dev_mode=False)
        assert db.get_match_threshold() == 0.3

    def test_user_set_writes_local_only(self, layers):
        _, system_yaml, _, local_yaml = layers
        _write_yaml(system_yaml, {"version": 1, "references": [],
                                  "match_threshold": 0.1})
        db = _make_db(layers, dev_mode=False)
        db.set_match_threshold(0.25)
        overlay = yaml.safe_load(local_yaml.read_text(encoding="utf-8"))
        assert overlay["match_threshold"] == 0.25
        # system 不动
        sys_doc = yaml.safe_load(system_yaml.read_text(encoding="utf-8"))
        assert sys_doc["match_threshold"] == 0.1

    def test_user_reset_to_system_clears_overlay(self, layers):
        _, system_yaml, _, local_yaml = layers
        _write_yaml(system_yaml, {"version": 1, "references": [],
                                  "match_threshold": 0.1})
        _write_yaml(local_yaml, {"version": 1, "references": [], "deleted": [],
                                 "match_threshold": 0.3})
        db = _make_db(layers, dev_mode=False)
        db.set_match_threshold(0.1)  # 回设为 system 值
        assert not local_yaml.exists()
        assert db.get_match_threshold() == 0.1

    def test_dev_set_writes_system(self, layers):
        _, system_yaml, _, local_yaml = layers
        _write_yaml(system_yaml, {"version": 1, "references": []})
        db = _make_db(layers, dev_mode=True)
        db.set_match_threshold(0.2)
        sys_doc = yaml.safe_load(system_yaml.read_text(encoding="utf-8"))
        assert sys_doc["match_threshold"] == 0.2
        assert not local_yaml.exists()

    def test_invalid_value_ignored(self, layers):
        _, system_yaml, _, _ = layers
        _write_yaml(system_yaml, {"version": 1, "references": [],
                                  "match_threshold": "abc"})
        db = _make_db(layers, dev_mode=False)
        assert db.get_match_threshold() == DEFAULT_MATCH_THRESHOLD


# ─── 开发模式写路由（对照）───────────────────────────────

class TestDevModeWrites:
    def test_add_and_remove_write_system(self, layers):
        system_dir, system_yaml, _, local_yaml = layers
        _write_yaml(system_yaml, {"version": 1, "references": []})
        db = _make_db(layers, dev_mode=True)
        img = np.zeros((2, 2, 3), dtype=np.uint8)
        entry = db.add_entry(label="甲", meta={"group": "g"}, image_data=img)
        assert (system_dir / entry.file).exists()
        assert not local_yaml.exists()

        assert db.remove_entry(entry.file) is True
        assert not (system_dir / entry.file).exists()
        sys_doc = yaml.safe_load(system_yaml.read_text(encoding="utf-8"))
        assert sys_doc["references"] == []
