"""ReferenceDatabase 双层覆盖单测：条目级合并 / schema 整替换 / 用户模式写路由

全部在 tmp_path 上用显式 override 构造，不触碰真实 config 目录。
"""

import json

import numpy as np
import pytest
import yaml

from lvjiang.core.reference_db import (
    DEFAULT_MATCH_THRESHOLD,
    DEFAULT_SPACE,
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
    # 名册/session 同步隔离到 tmp_path（避免读真实 config）：
    # 名册写入 DEFAULT_SPACE，与旧测试的空间级 yaml override 对齐
    system_spaces_yaml = system_dir.parent / "spaces.yaml"
    _write_yaml(system_spaces_yaml, {"version": 1, "spaces": [DEFAULT_SPACE]})
    local_spaces_yaml = local_dir.parent / "spaces.yaml"
    session_path = system_dir.parent / "session.json"
    return ReferenceDatabase(
        system_dir=system_dir, system_yaml=system_yaml,
        local_dir=local_dir, local_yaml=local_yaml,
        dev_mode=dev_mode,
        system_spaces_yaml=system_spaces_yaml,
        local_spaces_yaml=local_spaces_yaml,
        session_path=session_path,
    )


# ─── 桶发现 ──────────────────────────────────────────────

class TestBucketDiscovery:
    def test_discovers_all_subdirs_from_both_layers(self, layers):
        """扫描 local + system 层全部子目录，合并去重"""
        system_dir, system_yaml, local_dir, local_yaml = layers
        (system_dir / "bucket_a").mkdir(parents=True)
        (system_dir / "bucket_b").mkdir(parents=True)
        (local_dir / "bucket_b").mkdir(parents=True)  # 重复
        (local_dir / "bucket_c").mkdir(parents=True)
        (system_dir / ".hidden").mkdir(parents=True)  # 应被排除
        _write_yaml(system_yaml, {"version": 1, "references": []})
        _write_yaml(local_yaml, {"version": 1, "references": [], "deleted": []})
        db = _make_db(layers, dev_mode=False)
        assert db.buckets == ["bucket_a", "bucket_b", "bucket_c"]

    def test_empty_when_no_subdirs(self, layers):
        """无子目录时 buckets 为空列表"""
        system_dir, system_yaml, local_dir, local_yaml = layers
        _write_yaml(system_yaml, {"version": 1, "references": []})
        _write_yaml(local_yaml, {"version": 1, "references": [], "deleted": []})
        db = _make_db(layers, dev_mode=False)
        assert db.buckets == []

    def test_sorted_order(self, layers):
        """桶列表按字母排序"""
        system_dir, system_yaml, _, _ = layers
        (system_dir / "z_bucket").mkdir(parents=True)
        (system_dir / "a_bucket").mkdir(parents=True)
        (system_dir / "m_bucket").mkdir(parents=True)
        _write_yaml(system_yaml, {"version": 1, "references": []})
        db = _make_db(layers, dev_mode=False)
        assert db.buckets == ["a_bucket", "m_bucket", "z_bucket"]


# ─── 合并视图 ────────────────────────────────────────────

class TestMergedView:
    def test_union_of_layers(self, layers):
        _, system_yaml, _, local_yaml = layers
        _write_yaml(system_yaml, {"version": 1, "references": [
            _entry("A.png", "甲"), _entry("B.png", "乙")]})
        _write_yaml(local_yaml, {"version": 1, "references": [
            _entry("C.png", "丙")], "deleted": []})
        db = _make_db(layers, dev_mode=False)
        assert [e.file for e in db.entries] == ["A.png", "B.png", "C.png"]

    def test_local_entry_replaces_same_file(self, layers):
        _, system_yaml, _, local_yaml = layers
        _write_yaml(system_yaml, {"version": 1, "references": [
            _entry("A.png", "旧名", level=1)]})
        _write_yaml(local_yaml, {"version": 1, "references": [
            _entry("A.png", "新名", level=99)], "deleted": []})
        db = _make_db(layers, dev_mode=False)
        assert len(db.entries) == 1
        assert db.entries[0].label == "新名"
        assert db.entries[0].level == 99

    def test_deleted_removes_system_entry(self, layers):
        _, system_yaml, _, local_yaml = layers
        _write_yaml(system_yaml, {"version": 1, "references": [
            _entry("A.png", "甲"), _entry("B.png", "乙")]})
        _write_yaml(local_yaml, {"version": 1, "references": [],
                                 "deleted": ["A.png"]})
        db = _make_db(layers, dev_mode=False)
        assert [e.file for e in db.entries] == ["B.png"]

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
        system_dir, system_yaml, local_dir, local_yaml = layers
        # 创建桶目录（桶由目录扫描发现）
        (system_dir / "bucket_00").mkdir(parents=True)
        (system_dir / "bucket_00" / "A.png").write_bytes(b"sys")
        (local_dir / "bucket_00").mkdir(parents=True)
        (local_dir / "bucket_00" / "A.png").write_bytes(b"loc")
        _write_yaml(system_yaml, {"version": 1, "references": []})
        _write_yaml(local_yaml, {"version": 1, "references": [], "deleted": []})
        db = _make_db(layers, dev_mode=False)
        assert db.image_path("A.png") == local_dir / "bucket_00" / "A.png"

    def test_falls_back_to_system(self, layers):
        system_dir, system_yaml, _, _ = layers
        (system_dir / "bucket_00").mkdir(parents=True)
        _write_yaml(system_yaml, {"version": 1, "references": []})
        db = _make_db(layers, dev_mode=False)
        # 文件不存在时返回 system 层第一个桶的路径
        assert db.image_path("A.png") == system_dir / "bucket_00" / "A.png"


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
        (system_dir / "bucket_00").mkdir(parents=True)
        (system_dir / "bucket_00" / "A.png").write_bytes(b"sys")
        _write_yaml(system_yaml, {"version": 1, "references": [
            _entry("A.png", "甲")]})
        db = _make_db(layers, dev_mode=False)
        assert db.remove_entry("A.png") is True
        # system 图片与 yaml 均不动
        assert (system_dir / "bucket_00" / "A.png").exists()
        assert yaml.safe_load(system_yaml.read_text(
            encoding="utf-8"))["references"]
        overlay = yaml.safe_load(local_yaml.read_text(encoding="utf-8"))
        assert overlay["deleted"] == ["A.png"]
        assert db.entries == []

    def test_remove_local_only_entry_deletes_file(self, layers):
        _, system_yaml, local_dir, local_yaml = layers
        _write_yaml(system_yaml, {"version": 1, "references": []})
        (local_dir / "bucket_00").mkdir(parents=True)
        (local_dir / "bucket_00" / "B.png").write_bytes(b"loc")
        _write_yaml(local_yaml, {"version": 1, "references": [
            _entry("B.png", "乙")], "deleted": []})
        db = _make_db(layers, dev_mode=False)
        assert db.remove_entry("B.png") is True
        assert not (local_dir / "bucket_00" / "B.png").exists()
        # overlay 无内容 → 覆盖文件被删
        assert not local_yaml.exists()

    def test_update_system_entry_copies_to_local_shadow(self, layers):
        system_dir, system_yaml, _, local_yaml = layers
        (system_dir / "bucket_00").mkdir(parents=True)
        (system_dir / "bucket_00" / "A.png").write_bytes(b"sys")
        _write_yaml(system_yaml, {"version": 1, "references": [
            _entry("A.png", "旧名")]})
        db = _make_db(layers, dev_mode=False)
        assert db.update_entry("A.png", label="新名") is True
        overlay = yaml.safe_load(local_yaml.read_text(encoding="utf-8"))
        assert overlay["references"][0]["file"] == "A.png"  # file 不变
        assert overlay["references"][0]["label"] == "新名"
        # system yaml 原条目不动
        sys_doc = yaml.safe_load(system_yaml.read_text(encoding="utf-8"))
        assert sys_doc["references"][0]["label"] == "旧名"
        # 合并视图取影子
        assert db.get_entry("A.png").label == "新名"

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


# ─── meta_schema 输入/输出场景 ────────────────────────

class TestMetaSchemaScope:
    def test_parse_scope_and_crop(self, layers):
        _, system_yaml, _, _ = layers
        _write_yaml(system_yaml, {"version": 1, "references": [], "meta_schema": [
            {"key": "level", "name": "等级", "scope": "input"},
            {"key": "level_text", "name": "等级文本区域", "scope": "output",
             "crop": [0.0, 0.0, 1.0, 0.5]},
        ]})
        db = _make_db(layers, dev_mode=False)
        schema = db.get_meta_schema()
        assert schema[0].scope == "input"
        assert schema[0].crop is None
        assert schema[1].scope == "output"
        assert schema[1].crop == [0.0, 0.0, 1.0, 0.5]

    def test_missing_or_invalid_scope_defaults_to_input(self, layers):
        _, system_yaml, _, _ = layers
        _write_yaml(system_yaml, {"version": 1, "references": [], "meta_schema": [
            {"key": "a", "name": "A"},
            {"key": "b", "name": "B", "scope": "bogus"},
        ]})
        db = _make_db(layers, dev_mode=False)
        assert [f.scope for f in db.get_meta_schema()] == ["input", "input"]

    def test_invalid_crop_becomes_none(self, layers):
        _, system_yaml, _, _ = layers
        _write_yaml(system_yaml, {"version": 1, "references": [], "meta_schema": [
            {"key": "r1", "name": "R1", "scope": "output", "crop": [0.0, 0.0, 1.5, 0.5]},
            {"key": "r2", "name": "R2", "scope": "output", "crop": [0.0, 0.0, 1.0]},
            {"key": "r3", "name": "R3", "scope": "output", "crop": "abc"},
        ]})
        db = _make_db(layers, dev_mode=False)
        assert all(f.crop is None for f in db.get_meta_schema())

    def test_get_output_fields_filters_valid(self, layers):
        _, system_yaml, _, _ = layers
        _write_yaml(system_yaml, {"version": 1, "references": [], "meta_schema": [
            {"key": "level", "name": "等级", "scope": "input"},
            {"key": "level_text", "name": "等级文本区域", "scope": "output",
             "crop": [0.0, 0.0, 1.0, 0.5]},
            {"key": "bad", "name": "非法", "scope": "output", "crop": [2.0, 0, 1, 1]},
            {"key": "count_text", "name": "数量文本区域", "scope": "output",
             "crop": [0.0, 0.5, 1.0, 0.5]},
        ]})
        db = _make_db(layers, dev_mode=False)
        assert [f.key for f in db.get_output_fields()] == ["level_text", "count_text"]

    def test_save_omits_none_crop(self, layers):
        _, system_yaml, _, _ = layers
        _write_yaml(system_yaml, {"version": 1, "references": []})
        db = _make_db(layers, dev_mode=True)
        db.set_meta_schema([
            MetaFieldDef(key="level", name="等级", scope="input"),
            MetaFieldDef(key="level_text", name="等级文本区域", scope="output",
                         crop=[0.0, 0.0, 1.0, 0.5]),
        ])
        sys_doc = yaml.safe_load(system_yaml.read_text(encoding="utf-8"))
        fields = {f["key"]: f for f in sys_doc["meta_schema"]}
        assert "crop" not in fields["level"]           # input 字段不写 crop
        assert fields["level"]["scope"] == "input"
        assert fields["level_text"]["crop"] == [0.0, 0.0, 1.0, 0.5]


# ─── 图库空间 ────────────────────────────────────────

@pytest.fixture
def space_env(tmp_path, monkeypatch):
    """图库空间相关路径全部重定向到 tmp_path（完整路由隔离）

    reference_db 的层路径经 ConfigResolver 派生，打层根
    resolver.SYSTEM_CONFIG_DIR / resolver.LOCAL_CONFIG_DIR 即可（属性懒求值，monkeypatch 友好）。
    """
    import lvjiang.core.config.resolver as cr
    from lvjiang import constants
    system_root = tmp_path / "system"
    local_root = tmp_path / "local"
    session = tmp_path / "session" / "session.json"
    monkeypatch.setattr(cr, "SYSTEM_CONFIG_DIR", system_root)
    monkeypatch.setattr(cr, "LOCAL_CONFIG_DIR", local_root)
    monkeypatch.setattr(constants, "SESSION_PATH", session)
    system_ref = system_root / "references"
    return {
        "tmp": tmp_path,
        "system_ref": system_ref,          # system 空间 yaml + 图片根目录
        "local_ref": local_root / "references",
        "system_roster": system_root / "references.yaml",
        "local_roster": local_root / "references.yaml",
        "session": session,
    }


def _write_roster(path, spaces):
    _write_yaml(path, {"version": 1, "spaces": spaces})


class TestReferenceSpaces:
    def test_roster_merge_dedup_keep_order(self, space_env):
        """空间列表 = system 名册 ∪ local 名册，保序去重"""
        _write_roster(space_env["system_roster"], [DEFAULT_SPACE, "空间A"])
        _write_roster(space_env["local_roster"], ["空间A", "空间B"])
        db = ReferenceDatabase(dev_mode=False)
        assert db.get_spaces() == [DEFAULT_SPACE, "空间A", "空间B"]

    def test_empty_roster_falls_back_default(self, space_env):
        """名册全空回退 DEFAULT_SPACE"""
        db = ReferenceDatabase(dev_mode=False)
        assert db.get_spaces() == [DEFAULT_SPACE]
        assert db.get_active_space() == DEFAULT_SPACE

    def test_old_format_roster_detected_as_empty(self, space_env):
        """旧格式 references.yaml（含 references 键）不视为名册"""
        _write_yaml(space_env["system_roster"], {"version": 1, "references": []})
        db = ReferenceDatabase(dev_mode=False)
        assert db.get_spaces() == [DEFAULT_SPACE]

    def test_active_space_from_session(self, space_env):
        _write_roster(space_env["system_roster"], [DEFAULT_SPACE, "空间A"])
        space_env["session"].parent.mkdir(parents=True, exist_ok=True)
        space_env["session"].write_text(
            json.dumps({"active_space": "空间A"}), encoding="utf-8")
        db = ReferenceDatabase(dev_mode=False)
        assert db.get_active_space() == "空间A"

    def test_active_space_missing_or_invalid_falls_back_first(self, space_env):
        _write_roster(space_env["system_roster"], [DEFAULT_SPACE, "空间A"])
        db = ReferenceDatabase(dev_mode=False)
        assert db.get_active_space() == DEFAULT_SPACE  # 无 session
        space_env["session"].parent.mkdir(parents=True, exist_ok=True)
        space_env["session"].write_text(
            json.dumps({"active_space": "不存在"}), encoding="utf-8")
        db2 = ReferenceDatabase(dev_mode=False)
        assert db2.get_active_space() == DEFAULT_SPACE  # 非法回退名册首个

    def test_space_yaml_load_routing(self, space_env):
        """不同空间加载各自 yaml：条目互不可见"""
        _write_roster(space_env["system_roster"], [DEFAULT_SPACE, "空间A"])
        _write_yaml(space_env["system_ref"] / f"{DEFAULT_SPACE}.yaml",
                    {"version": 1, "references": [_entry("g/A.png", "甲")]})
        _write_yaml(space_env["system_ref"] / "空间A.yaml",
                    {"version": 1, "references": [_entry("g/B.png", "乙")]})
        db = ReferenceDatabase(dev_mode=False)
        assert [e.label for e in db.entries] == ["甲"]
        assert db.set_active_space("空间A") is True
        db.load()
        assert [e.label for e in db.entries] == ["乙"]

    def test_set_active_space_persists_session(self, space_env):
        """切换写 session.json（保留其他字段）；非法名拒绝"""
        _write_roster(space_env["system_roster"], [DEFAULT_SPACE, "空间A"])
        space_env["session"].parent.mkdir(parents=True, exist_ok=True)
        space_env["session"].write_text(
            json.dumps({"active_user": "tester"}), encoding="utf-8")
        db = ReferenceDatabase(dev_mode=False)
        assert db.set_active_space("幽灵") is False  # 名册外拒绝
        assert db.set_active_space("空间A") is True
        data = json.loads(space_env["session"].read_text(encoding="utf-8"))
        assert data["active_space"] == "空间A"
        assert data["active_user"] == "tester"

    def test_save_writes_active_space_yaml(self, space_env):
        """dev 模式 save 落盘到激活空间的 yaml"""
        _write_roster(space_env["system_roster"], [DEFAULT_SPACE, "空间A"])
        space_env["session"].parent.mkdir(parents=True, exist_ok=True)
        space_env["session"].write_text(
            json.dumps({"active_space": "空间A"}), encoding="utf-8")
        db = ReferenceDatabase(dev_mode=True)
        img = np.zeros((2, 2, 3), dtype=np.uint8)
        entry = db.add_entry(label="甲", meta={"group": "g"}, image_data=img)
        # 条目与图片都落在空间A 目录下（分组结构保留）
        doc = yaml.safe_load((space_env["system_ref"] / "空间A.yaml")
                             .read_text(encoding="utf-8"))
        assert [e["file"] for e in doc["references"]] == [entry.file]
        assert (space_env["system_ref"] / "空间A" / entry.file).exists()

    def test_image_path_resolves_within_active_space(self, space_env):
        """图片路径按激活空间解析，local 优先"""
        _write_roster(space_env["system_roster"], [DEFAULT_SPACE, "空间A"])
        # 创建桶目录（桶由目录扫描发现）
        (space_env["system_ref"] / "空间A" / "bucket_00").mkdir(parents=True)
        (space_env["system_ref"] / "空间A" / "bucket_00" / "X.png").write_bytes(b"sys")
        (space_env["local_ref"] / "空间A" / "bucket_00").mkdir(parents=True)
        (space_env["local_ref"] / "空间A" / "bucket_00" / "X.png").write_bytes(b"loc")
        system_yaml = space_env["system_ref"] / "空间A.yaml"
        local_yaml = space_env["local_ref"] / "空间A.yaml"
        _write_yaml(system_yaml, {"version": 1, "references": []})
        _write_yaml(local_yaml, {"version": 1, "references": [], "deleted": []})
        db = ReferenceDatabase(dev_mode=False)
        db.set_active_space("空间A")
        db.load()
        assert db.image_path("X.png") == (
            space_env["local_ref"] / "空间A" / "bucket_00" / "X.png")
        # local 缺失时回退 system
        assert db.image_path("Y.png") == (
            space_env["system_ref"] / "空间A" / "bucket_00" / "Y.png")

    def test_create_space_user_mode(self, space_env):
        """用户模式新建空间：yaml 落 local 层并注册 local 名册"""
        _write_roster(space_env["system_roster"], [DEFAULT_SPACE])
        db = ReferenceDatabase(dev_mode=False)
        assert db.create_space("新空间") is True
        assert db.create_space("新空间") is False  # 重名拒绝
        space_yaml = space_env["local_ref"] / "新空间.yaml"
        assert space_yaml.exists()
        doc = yaml.safe_load(space_yaml.read_text(encoding="utf-8"))
        assert doc["references"] == []
        assert doc["meta_schema"] == []  # 新建空间无预填字段，避免业务侵入
        local_roster = yaml.safe_load(
            space_env["local_roster"].read_text(encoding="utf-8"))
        assert local_roster["spaces"] == ["新空间"]
        assert db.get_spaces() == [DEFAULT_SPACE, "新空间"]

    def test_create_space_dev_mode(self, space_env):
        """开发模式新建空间：yaml 落 system 层并注册 system 名册"""
        _write_roster(space_env["system_roster"], [DEFAULT_SPACE])
        db = ReferenceDatabase(dev_mode=True)
        assert db.create_space("新空间") is True
        assert (space_env["system_ref"] / "新空间.yaml").exists()
        roster = yaml.safe_load(
            space_env["system_roster"].read_text(encoding="utf-8"))
        assert roster["spaces"] == [DEFAULT_SPACE, "新空间"]

    def test_create_space_rejects_old_format_roster(self, space_env):
        """旧格式名册（未迁移的覆盖层）拒绝覆写，数据不被销毁"""
        _write_roster(space_env["system_roster"], [DEFAULT_SPACE])
        legacy = {"version": 1,
                  "references": [_entry("g/A.png", "甲")],
                  "deleted": ["g/B.png"]}
        _write_yaml(space_env["local_roster"], legacy)
        db = ReferenceDatabase(dev_mode=False)  # 用户模式走 local 名册
        assert db.create_space("新空间") is False
        # 旧文件原样保留，空间 yaml 也不落盘
        assert yaml.safe_load(space_env["local_roster"].read_text(
            encoding="utf-8")) == legacy
        assert not (space_env["local_ref"] / "新空间.yaml").exists()
        assert "新空间" not in db.get_spaces()
