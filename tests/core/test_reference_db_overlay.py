"""ReferenceDatabase 双层覆盖单测：条目级合并 / schema 整替换 / 用户模式写路由

全部在 tmp_path 上用显式 override 构造，不触碰真实 config 目录。
"""

import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from lvjiang.core.paths import CONFIG_SUBDIRECTORIES
from lvjiang.core.reference_db import (
    DEFAULT_MATCH_THRESHOLD,
    DEFAULT_SPACE,
    UNGROUPED_DIR,
    MetaFieldDef,
    ReferenceDatabase,
    validate_group_names,
)
from tests.case_matrix import case_matrix


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
    # 空间扫描/session 隔离到 tmp_path（避免读真实 config）：
    # 指向空目录 → 空间列表回退 DEFAULT_SPACE，与本组的空间级 yaml override 对齐
    session_path = system_dir.parent / "session.json"
    return ReferenceDatabase(
        system_dir=system_dir, system_yaml=system_yaml,
        local_dir=local_dir, local_yaml=local_yaml,
        dev_mode=dev_mode,
        system_spaces_dir=system_dir.parent / "spaces_probe",
        local_spaces_dir=local_dir.parent / "spaces_probe",
        session_path=session_path,
    )


# ─── 图片目录（即分组目录）发现 ──────────────────────────

class TestImageDirDiscovery:
    def test_discovers_all_subdirs_from_both_layers(self, layers):
        """扫描 local + system 层全部子目录，合并去重"""
        system_dir, system_yaml, local_dir, local_yaml = layers
        (system_dir / "调律材料").mkdir(parents=True)
        (system_dir / "装备培养").mkdir(parents=True)
        (local_dir / "装备培养").mkdir(parents=True)  # 重复
        (local_dir / "货币资产").mkdir(parents=True)
        (system_dir / ".hidden").mkdir(parents=True)  # 应被排除
        _write_yaml(system_yaml, {"version": 1, "references": []})
        _write_yaml(local_yaml, {"version": 1, "references": [], "deleted": []})
        db = _make_db(layers, dev_mode=False)
        assert db.image_dirs == sorted(["调律材料", "装备培养", "货币资产"])


# ─── 合并视图 ────────────────────────────────────────────

class TestMergedView:

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


# ─── image_path 解析 ─────────────────────────────────────


# ─── 用户模式写路由 ──────────────────────────────────────

class TestUserModeWrites:
    def test_add_entry_image_lands_in_local_group_dir(self, layers):
        _, system_yaml, local_dir, local_yaml = layers
        _write_yaml(system_yaml, {"version": 1, "references": []})
        db = _make_db(layers, dev_mode=False)
        img = np.zeros((2, 2, 3), dtype=np.uint8)
        entry = db.add_entry(label="定音石", meta={"group": "调律材料"},
                             image_data=img)
        assert (local_dir / "调律材料" / entry.file).exists()
        overlay = yaml.safe_load(local_yaml.read_text(encoding="utf-8"))
        assert [e["file"] for e in overlay["references"]] == [entry.file]
        # 合并视图可见
        assert db.get_entry(entry.file) is not None

    def test_remove_system_entry_refused(self, layers):
        """系统参考图不允许用户删除——想要自己的一套请新建图库空间。"""
        from lvjiang.core.config.resolver import SystemContentProtected

        system_dir, system_yaml, _, local_yaml = layers
        (system_dir / "bucket_00").mkdir(parents=True)
        (system_dir / "bucket_00" / "A.png").write_bytes(b"sys")
        _write_yaml(system_yaml, {"version": 1, "references": [
            _entry("A.png", "甲")]})
        db = _make_db(layers, dev_mode=False)
        with pytest.raises(SystemContentProtected):
            db.remove_entry("A.png")
        # 图片、名册、合并视图都不受影响
        assert (system_dir / "bucket_00" / "A.png").exists()
        assert yaml.safe_load(system_yaml.read_text(
            encoding="utf-8"))["references"]
        assert not local_yaml.exists()
        assert [e.file for e in db.entries] == ["A.png"]

    def test_remove_own_local_entry_allowed(self, layers):
        """用户自己加的条目照常可删。"""
        _, system_yaml, local_dir, local_yaml = layers
        _write_yaml(system_yaml, {"version": 1, "references": []})
        (local_dir / "bucket_00").mkdir(parents=True)
        (local_dir / "bucket_00" / "B.png").write_bytes(b"mine")
        _write_yaml(local_yaml, {"version": 1, "references": [
            _entry("B.png", "乙")]})
        db = _make_db(layers, dev_mode=False)
        assert db.remove_entry("B.png") is True
        assert db.entries == []


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
        assert (system_dir / "g" / entry.file).exists()
        assert not local_yaml.exists()

        assert db.remove_entry(entry.file) is True
        assert not (system_dir / "g" / entry.file).exists()
        sys_doc = yaml.safe_load(system_yaml.read_text(encoding="utf-8"))
        assert sys_doc["references"] == []


# ─── 分组 → 图片目录 ─────────────────────────────────────

class TestGroupDirectories:
    """图片目录即分组名：写入取第一个分组，未分组落 default，改分组挪图。"""

    def test_ungrouped_entry_lands_in_default_dir(self, layers):
        """无分组条目的图片落 default/，仍能按文件名解析到。"""
        _, system_yaml, local_dir, _ = layers
        _write_yaml(system_yaml, {"version": 1, "references": []})
        db = _make_db(layers, dev_mode=False)
        entry = db.add_entry(
            label="甲", image_data=np.zeros((2, 2, 3), dtype=np.uint8))
        assert (local_dir / UNGROUPED_DIR / entry.file).exists()
        assert db.image_path(entry.file) == local_dir / UNGROUPED_DIR / entry.file

    def test_first_group_decides_directory(self, layers):
        """多分组条目只落第一个分组对应的目录。"""
        _, system_yaml, local_dir, _ = layers
        _write_yaml(system_yaml, {"version": 1, "references": []})
        db = _make_db(layers, dev_mode=False)
        entry = db.add_entry(label="甲", meta={"group": "甲组, 乙组"},
                             image_data=np.zeros((2, 2, 3), dtype=np.uint8))
        assert (local_dir / "甲组" / entry.file).exists()
        assert not (local_dir / "乙组").exists()

    def test_group_change_moves_image_in_writable_layer(self, layers):
        """开发模式改分组：图片跟着挪进新分组目录，旧目录不再有该文件。"""
        system_dir, system_yaml, _, _ = layers
        _write_yaml(system_yaml, {"version": 1, "references": []})
        db = _make_db(layers, dev_mode=True)
        entry = db.add_entry(label="甲", meta={"group": "旧组"},
                             image_data=np.zeros((2, 2, 3), dtype=np.uint8))
        assert db.update_entry(entry.file, meta={"group": "新组"}) is True
        assert (system_dir / "新组" / entry.file).exists()
        assert not (system_dir / "旧组" / entry.file).exists()

    def test_user_mode_group_change_keeps_system_image(self, layers):
        """用户模式改系统条目分组：只写 local 影子，系统图片原地不动。"""
        system_dir, system_yaml, _, _ = layers
        (system_dir / "旧组").mkdir(parents=True)
        (system_dir / "旧组" / "A.png").write_bytes(b"sys")
        _write_yaml(system_yaml, {"version": 1, "references": [
            _entry("A.png", "甲", group="旧组")]})
        db = _make_db(layers, dev_mode=False)
        assert db.update_entry("A.png", meta={"group": "新组"}) is True
        assert (system_dir / "旧组" / "A.png").exists()
        assert not (system_dir / "新组" / "A.png").exists()
        assert db.get_entry("A.png").group == "新组"

    @case_matrix("bad", [
        "default", "Default", "甲组,default", ".隐藏", "a/b", "a\\b", "a|b",
    ])
    def test_illegal_group_name_rejected_before_writing(self, layers, bad):
        """保留名与非法分组名在写图片之前就拒绝，条目和文件都不落地。"""
        _, system_yaml, local_dir, _ = layers
        _write_yaml(system_yaml, {"version": 1, "references": []})
        db = _make_db(layers, dev_mode=False)
        with pytest.raises(ValueError):
            db.add_entry(label="甲", meta={"group": bad},
                         image_data=np.zeros((2, 2, 3), dtype=np.uint8))
        assert db.entries == []
        assert not list(local_dir.rglob("*.png"))

    def test_validate_group_names_normalizes_text(self):
        """校验函数只判合法性并返回去空白后的规范文本。"""
        assert validate_group_names("  甲组 , 乙组  ") == "甲组,乙组"
        assert validate_group_names("") == ""
        assert validate_group_names(None) == ""


# ─── 数字 meta 归一 ──────────────────────────────────────

class TestNumericMetaCoercion:
    """录入界面按文本收集 meta，写入口必须按 schema 归一成数字。"""

    @staticmethod
    def _schema_doc(*, extra: dict | None = None) -> dict:
        return {
            "version": 1,
            "meta_schema": [
                {"key": "level", "name": "等级", "type": "number", "scope": "input"},
                {"key": "note", "name": "备注字段", "type": "text", "scope": "input"},
            ],
            "references": [extra] if extra else [],
        }

    def test_add_entry_stores_numeric_text_as_int(self, layers):
        """'110' 落盘为 110：同一图库不会同时存在两种写法。"""
        _, system_yaml, _, _ = layers
        _write_yaml(system_yaml, self._schema_doc())
        db = _make_db(layers, dev_mode=True)
        entry = db.add_entry(
            label="甲", meta={"group": "g", "level": "110"},
            image_data=np.zeros((2, 2, 3), dtype=np.uint8))
        assert entry.level == 110
        assert isinstance(entry.meta["level"], int)
        stored = yaml.safe_load(system_yaml.read_text(encoding="utf-8"))
        assert stored["references"][0]["meta"]["level"] == 110
        # 空文本 = 未填：不落空串
        blank = db.add_entry(
            label="乙", meta={"group": "g", "level": "  "},
            image_data=np.zeros((2, 2, 3), dtype=np.uint8))
        assert "level" not in blank.meta

    def test_update_entry_coerces_text_level(self, layers):
        """改等级按文本提交同样转数字；空文本视为未修改，不写空串。"""
        system_dir, system_yaml, _, _ = layers
        (system_dir / "g").mkdir(parents=True)
        (system_dir / "g" / "A.png").write_bytes(b"sys")
        _write_yaml(system_yaml, self._schema_doc(
            extra=_entry("A.png", "甲", group="g", level=110)))
        db = _make_db(layers, dev_mode=True)
        assert db.update_entry("A.png", meta={"level": "105"}) is True
        assert db.get_entry("A.png").level == 105
        assert isinstance(db.get_entry("A.png").meta["level"], int)

        assert db.update_entry("A.png", meta={"level": "  "}) is True
        assert db.get_entry("A.png").level == 105  # 未填 → 保持原值

    def test_non_numeric_text_is_kept_as_is(self, layers):
        """解析不出来的文本原样保留——不能悄悄吞掉用户输入。"""
        system_dir, system_yaml, _, _ = layers
        (system_dir / "g").mkdir(parents=True)
        (system_dir / "g" / "A.png").write_bytes(b"sys")
        _write_yaml(system_yaml, self._schema_doc(
            extra=_entry("A.png", "甲", group="g")))
        db = _make_db(layers, dev_mode=True)
        assert db.update_entry("A.png", meta={"level": "待定"}) is True
        assert db.get_entry("A.png").meta["level"] == "待定"

    def test_text_field_is_untouched(self, layers):
        """非 number 字段不做数字转换。"""
        system_dir, system_yaml, _, _ = layers
        (system_dir / "g").mkdir(parents=True)
        (system_dir / "g" / "A.png").write_bytes(b"sys")
        _write_yaml(system_yaml, self._schema_doc(
            extra=_entry("A.png", "甲", group="g")))
        db = _make_db(layers, dev_mode=True)
        assert db.update_entry("A.png", meta={"note": "110"}) is True
        assert db.get_entry("A.png").meta["note"] == "110"


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
    core.paths 的层目录即可（属性懒求值，monkeypatch 友好）。
    """
    from lvjiang import constants
    system_root = tmp_path / "system"
    local_root = tmp_path / "local"
    session = tmp_path / "session" / "session.json"
    monkeypatch.setitem(CONFIG_SUBDIRECTORIES, "system", system_root)
    monkeypatch.setitem(CONFIG_SUBDIRECTORIES, "local", local_root)
    monkeypatch.setattr(constants, "SESSION_PATH", session)
    system_ref = system_root / "references"
    return {
        "tmp": tmp_path,
        "system_ref": system_ref,          # system 空间 yaml + 图片根目录
        "local_ref": local_root / "references",
        "legacy_roster": local_root / "references.yaml",
        "session": session,
        "interface": session.with_name("interface.json"),
    }


def _declare_spaces(ref_dir, spaces):
    """在某层 references 目录下落空间 yaml —— 落盘即注册"""
    for name in spaces:
        _write_yaml(ref_dir / f"{name}.yaml", {"version": 1, "references": []})


class TestReferenceSpaces:
    def test_factory_spaces_use_stable_keys_with_localized_names(self):
        """随包空间用 desktop/android 作稳定 key，中文名只作展示。"""
        references_dir = (
            Path(__file__).parents[2] / "config" / "system" / "references"
        )

        assert DEFAULT_SPACE == "android"
        assert sorted(path.stem for path in references_dir.glob("*.yaml")) == [
            "android", "desktop",
        ]
        assert not (references_dir / "默认.yaml").exists()
        assert not (references_dir / "默认").exists()
        # 图片目录与 key 同名（目录即空间）
        for key in ("android", "desktop"):
            assert (references_dir / key).is_dir()
        # 展示名是双语映射，且与 app.yaml 里的方案/环境 key 对齐
        import yaml as _yaml
        for key, zh in (("android", "手游"), ("desktop", "端游")):
            doc = _yaml.safe_load(
                (references_dir / f"{key}.yaml").read_text(encoding="utf-8"))
            assert doc["name"]["zh_CN"] == zh
            assert doc["name"]["en_US"]

    def test_space_label_resolves_localized_name(self, space_env):
        """展示名读 yaml 顶层 name：dict 按语言取值，纯字符串/缺失回落 key。"""
        ref = space_env["system_ref"]
        _write_yaml(ref / "android.yaml", {
            "version": 1, "name": {"zh_CN": "手游", "en_US": "Mobile"},
            "references": []})
        _write_yaml(ref / "legacy.yaml", {
            "version": 1, "name": "旧空间", "references": []})
        _write_yaml(ref / "plain.yaml", {"version": 1, "references": []})
        db = ReferenceDatabase(dev_mode=False)
        assert db.space_label("android") == "手游"
        assert db.space_label("legacy") == "旧空间"
        assert db.space_label("plain") == "plain"
        assert db.space_label("missing") == "missing"

    def test_save_keeps_localized_space_name(self, space_env):
        """保存条目不能把随包的双语空间名抹掉。"""
        ref = space_env["system_ref"]
        _write_yaml(ref / "android.yaml", {
            "version": 1, "name": {"zh_CN": "手游", "en_US": "Mobile"},
            "references": []})
        db = ReferenceDatabase(dev_mode=True)
        db.add_entry(label="甲", meta={"group": "g"},
                     image_data=np.zeros((2, 2, 3), dtype=np.uint8))
        doc = yaml.safe_load((ref / "android.yaml").read_text(encoding="utf-8"))
        assert doc["name"] == {"zh_CN": "手游", "en_US": "Mobile"}
        assert len(doc["references"]) == 1


class TestPreviewWithoutActivating:
    """管理界面切换"正在看的空间"不得改写运行时空间（actives.space）。"""

    def test_open_space_keeps_actives_untouched(self, space_env):
        _declare_spaces(space_env["system_ref"], [DEFAULT_SPACE, "空间A"])
        db = ReferenceDatabase(dev_mode=False)
        assert db.runtime_space() == DEFAULT_SPACE
        assert db.open_space("空间A") is True
        assert db.get_active_space() == "空间A"        # 本实例：正在编辑
        assert db.runtime_space() == DEFAULT_SPACE     # 运行时：没动
        assert not space_env["interface"].exists()     # 连 interface 都没写

    def test_deleting_the_runtime_space_repoints_actives(self, space_env):
        """删的是运行时空间：即使此刻正在看另一个，也要改 actives 并重载。"""
        _declare_spaces(space_env["local_ref"], ["甲", "乙"])
        space_env["interface"].parent.mkdir(parents=True, exist_ok=True)
        space_env["interface"].write_text(
            json.dumps({"actives": {"space": "甲"}}), encoding="utf-8")
        db = ReferenceDatabase(dev_mode=False)
        assert db.get_active_space() == "甲"
        assert db.open_space("乙") is True
        assert db.delete_space("甲") is True
        data = json.loads(space_env["interface"].read_text(encoding="utf-8"))
        assert data["actives"]["space"] == "乙"
        assert db.get_active_space() == "乙"

    def test_deleting_the_preview_space_keeps_actives(self, space_env):
        """删的只是正在看的那个：运行空间不变，本实例回到它。"""
        _declare_spaces(space_env["local_ref"], ["甲", "乙"])
        space_env["interface"].parent.mkdir(parents=True, exist_ok=True)
        space_env["interface"].write_text(
            json.dumps({"actives": {"space": "甲"}}), encoding="utf-8")
        db = ReferenceDatabase(dev_mode=False)
        assert db.open_space("乙") is True
        assert db.delete_space("乙") is True
        data = json.loads(space_env["interface"].read_text(encoding="utf-8"))
        assert data["actives"]["space"] == "甲"
        assert db.get_active_space() == "甲"


class TestSpaceDisplayName:
    """空间展示名可编辑（多语言），key 不可改、条目不变。"""

    def test_dev_mode_writes_name_into_system_yaml(self, space_env):
        ref = space_env["system_ref"]
        _write_yaml(ref / "android.yaml", {
            "version": 1, "references": [_entry("A.png", "甲", group="g")]})
        db = ReferenceDatabase(dev_mode=True)
        assert db.set_space_name("android", {"zh_CN": "手游", "en_US": "Mobile"}) is True
        doc = yaml.safe_load((ref / "android.yaml").read_text(encoding="utf-8"))
        assert doc["name"] == {"zh_CN": "手游", "en_US": "Mobile"}
        assert [e["file"] for e in doc["references"]] == ["A.png"]  # 条目原样
        assert db.space_label("android") == "手游"

    def test_user_mode_writes_local_overlay_only(self, space_env):
        """用户模式改系统空间的展示名：只写 local 覆盖层，系统文件与条目都不动。"""
        sys_ref = space_env["system_ref"]
        loc_ref = space_env["local_ref"]
        _write_yaml(sys_ref / "android.yaml", {
            "version": 1, "references": [_entry("A.png", "甲", group="g")]})
        db = ReferenceDatabase(dev_mode=False)
        assert db.set_space_name("android", {"zh_CN": "手机端"}) is True
        overlay = yaml.safe_load(
            (loc_ref / "android.yaml").read_text(encoding="utf-8"))
        assert overlay["name"] == {"zh_CN": "手机端"}
        sys_doc = yaml.safe_load(
            (sys_ref / "android.yaml").read_text(encoding="utf-8"))
        assert "name" not in sys_doc
        # 只有 name 的覆盖层对条目合并是恒等操作
        assert [e.file for e in db.entries] == ["A.png"]
        assert db.space_label("android") == "手机端"

    def test_create_space_writes_display_name_once(self, space_env):
        """新建空间时展示名与空间写进同一份 yaml，不留「空间已建、名字没落下」。"""
        db = ReferenceDatabase(dev_mode=False)
        assert db.create_space(
            "myspace", {"zh_CN": "我的空间", "en_US": "My Space"}) is True
        path = space_env["local_ref"] / "myspace.yaml"
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert doc["name"] == {"zh_CN": "我的空间", "en_US": "My Space"}
        assert doc["references"] == []
        assert db.space_label("myspace") == "我的空间"

    def test_none_clears_name_and_unknown_space_is_refused(self, space_env):
        ref = space_env["system_ref"]
        _write_yaml(ref / "android.yaml", {
            "version": 1, "name": {"zh_CN": "手游"}, "references": []})
        db = ReferenceDatabase(dev_mode=True)
        assert db.set_space_name("android", None) is True
        doc = yaml.safe_load((ref / "android.yaml").read_text(encoding="utf-8"))
        assert "name" not in doc
        assert db.space_label("android") == "android"   # 回落到 key
        assert db.set_space_name("幽灵", {"zh_CN": "幽灵"}) is False

    def test_spaces_scanned_from_both_layers(self, space_env):
        """空间列表 = system 目录扫描 ∪ local 目录扫描，各自按名排序去重"""
        _declare_spaces(space_env["system_ref"], [DEFAULT_SPACE, "空间A"])
        _declare_spaces(space_env["local_ref"], ["空间A", "空间B"])
        db = ReferenceDatabase(dev_mode=False)
        assert db.get_spaces() == [DEFAULT_SPACE, "空间A", "空间B"]
        # local 同名 yaml 是覆盖层，不产生第二个空间
        assert db.get_spaces().count("空间A") == 1

    def test_no_space_yaml_falls_back_default(self, space_env):
        """两层都没有空间 yaml 时回退 DEFAULT_SPACE"""
        db = ReferenceDatabase(dev_mode=False)
        assert db.get_spaces() == [DEFAULT_SPACE]
        assert db.get_active_space() == DEFAULT_SPACE


    def test_default_space_preferred_over_first(self, space_env):
        """无 session 记录时优先激活 DEFAULT_SPACE，而非排序首个"""
        _declare_spaces(space_env["system_ref"], [DEFAULT_SPACE, "AAA"])
        db = ReferenceDatabase(dev_mode=False)
        assert db.get_spaces()[0] == "AAA"
        assert db.get_active_space() == DEFAULT_SPACE

    def test_active_space_from_interface(self, space_env):
        _declare_spaces(space_env["system_ref"], [DEFAULT_SPACE, "空间A"])
        space_env["interface"].parent.mkdir(parents=True, exist_ok=True)
        space_env["interface"].write_text(
            json.dumps({"actives": {"space": "空间A"}}), encoding="utf-8")
        db = ReferenceDatabase(dev_mode=False)
        assert db.get_active_space() == "空间A"

    def test_active_space_missing_or_invalid_falls_back_default(self, space_env):
        _declare_spaces(space_env["system_ref"], [DEFAULT_SPACE, "空间A"])
        db = ReferenceDatabase(dev_mode=False)
        assert db.get_active_space() == DEFAULT_SPACE  # 无记录
        space_env["interface"].parent.mkdir(parents=True, exist_ok=True)
        space_env["interface"].write_text(
            json.dumps({"actives": {"space": "不存在"}}), encoding="utf-8")
        db2 = ReferenceDatabase(dev_mode=False)
        assert db2.get_active_space() == DEFAULT_SPACE  # 非法回退 DEFAULT_SPACE

    def test_space_yaml_load_routing(self, space_env):
        """不同空间加载各自 yaml：条目互不可见"""
        _write_yaml(space_env["system_ref"] / f"{DEFAULT_SPACE}.yaml",
                    {"version": 1, "references": [_entry("g/A.png", "甲")]})
        _write_yaml(space_env["system_ref"] / "空间A.yaml",
                    {"version": 1, "references": [_entry("g/B.png", "乙")]})
        db = ReferenceDatabase(dev_mode=False)
        assert [e.label for e in db.entries] == ["甲"]
        assert db.set_active_space("空间A") is True
        db.load()
        assert [e.label for e in db.entries] == ["乙"]

    def test_set_active_space_persists_interface(self, space_env):
        """切换写 interface.json（保留其他字段）；非法名拒绝"""
        _declare_spaces(space_env["system_ref"], [DEFAULT_SPACE, "空间A"])
        space_env["interface"].parent.mkdir(parents=True, exist_ok=True)
        space_env["interface"].write_text(
            json.dumps({"actives": {"user": "tester"}}), encoding="utf-8")
        db = ReferenceDatabase(dev_mode=False)
        assert db.set_active_space("幽灵") is False  # 未扫到的空间拒绝
        assert db.set_active_space("空间A") is True
        data = json.loads(space_env["interface"].read_text(encoding="utf-8"))
        assert data["actives"] == {"space": "空间A", "user": "tester"}

    def test_save_writes_active_space_yaml(self, space_env):
        """dev 模式 save 落盘到激活空间的 yaml"""
        _declare_spaces(space_env["system_ref"], [DEFAULT_SPACE, "空间A"])
        space_env["interface"].parent.mkdir(parents=True, exist_ok=True)
        space_env["interface"].write_text(
            json.dumps({"actives": {"space": "空间A"}}), encoding="utf-8")
        db = ReferenceDatabase(dev_mode=True)
        img = np.zeros((2, 2, 3), dtype=np.uint8)
        entry = db.add_entry(label="甲", meta={"group": "g"}, image_data=img)
        # 条目与图片都落在空间A 目录下（图片按分组落目录）
        doc = yaml.safe_load((space_env["system_ref"] / "空间A.yaml")
                             .read_text(encoding="utf-8"))
        assert [e["file"] for e in doc["references"]] == [entry.file]
        assert (space_env["system_ref"] / "空间A" / "g" / entry.file).exists()

    def test_image_path_resolves_within_active_space(self, space_env):
        """图片路径按激活空间解析，local 优先"""
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


    def test_create_space_rejects_system_space_name(self, space_env):
        """用户模式不能用系统空间名新建（同名 yaml 是覆盖层，不是新空间）"""
        _declare_spaces(space_env["system_ref"], [DEFAULT_SPACE])
        db = ReferenceDatabase(dev_mode=False)
        assert db.create_space(DEFAULT_SPACE) is False
        assert not (space_env["local_ref"] / f"{DEFAULT_SPACE}.yaml").exists()

    @case_matrix("bad", ["", "  ", ".隐藏", "a/b", "a\\b", "a:b", "a*b"])
    def test_create_space_rejects_illegal_name(self, space_env, bad):
        """空间名即文件名：拒空、拒 . 开头、拒路径分隔符"""
        db = ReferenceDatabase(dev_mode=False)
        assert db.create_space(bad) is False


class TestDeleteSpace:
    def test_user_mode_deletes_local_space_with_images(self, space_env):
        """用户模式删本地空间：yaml 与同名图片目录一并清掉"""
        _declare_spaces(space_env["system_ref"], [DEFAULT_SPACE])
        db = ReferenceDatabase(dev_mode=False)
        assert db.create_space("我的空间") is True
        img_dir = space_env["local_ref"] / "我的空间" / "bucket_0"
        img_dir.mkdir(parents=True)
        (img_dir / "A.png").write_bytes(b"x")

        assert db.can_delete_space("我的空间") == ""
        assert db.delete_space("我的空间") is True
        assert not (space_env["local_ref"] / "我的空间.yaml").exists()
        assert not (space_env["local_ref"] / "我的空间").exists()
        assert db.get_spaces() == [DEFAULT_SPACE]

    def test_user_mode_refuses_system_space(self, space_env):
        """用户模式拒删系统空间，文件原样保留"""
        _declare_spaces(space_env["system_ref"], [DEFAULT_SPACE, "空间A"])
        db = ReferenceDatabase(dev_mode=False)
        assert db.can_delete_space("空间A") != ""
        assert db.delete_space("空间A") is False
        assert (space_env["system_ref"] / "空间A.yaml").exists()
        assert "空间A" in db.get_spaces()

    def test_user_mode_refuses_system_space_with_local_overlay(self, space_env):
        """系统空间即便有 local 覆盖层也不可删（覆盖层不是本地空间）"""
        _declare_spaces(space_env["system_ref"], [DEFAULT_SPACE, "空间A"])
        _declare_spaces(space_env["local_ref"], ["空间A"])
        db = ReferenceDatabase(dev_mode=False)
        assert db.delete_space("空间A") is False
        assert (space_env["local_ref"] / "空间A.yaml").exists()

    def test_dev_mode_deletes_both_layers(self, space_env):
        """开发模式删系统空间：两层一起清，不留孤儿覆盖层"""
        _declare_spaces(space_env["system_ref"], [DEFAULT_SPACE, "空间A"])
        _declare_spaces(space_env["local_ref"], ["空间A"])
        db = ReferenceDatabase(dev_mode=True)
        assert db.delete_space("空间A") is True
        assert not (space_env["system_ref"] / "空间A.yaml").exists()
        assert not (space_env["local_ref"] / "空间A.yaml").exists()
        assert db.get_spaces() == [DEFAULT_SPACE]

    def test_refuses_last_space(self, space_env):
        """至少保留一个空间"""
        _declare_spaces(space_env["system_ref"], [DEFAULT_SPACE])
        db = ReferenceDatabase(dev_mode=True)
        assert db.can_delete_space(DEFAULT_SPACE) != ""
        assert db.delete_space(DEFAULT_SPACE) is False
        assert (space_env["system_ref"] / f"{DEFAULT_SPACE}.yaml").exists()

    def test_refuses_unknown_and_illegal_name(self, space_env):
        _declare_spaces(space_env["system_ref"], [DEFAULT_SPACE, "空间A"])
        db = ReferenceDatabase(dev_mode=True)
        assert db.can_delete_space("幽灵") != ""
        assert db.delete_space("幽灵") is False
        assert db.delete_space("../系统") is False  # 路径穿越被空间名校验拦下

    def test_deleting_active_space_switches_and_reloads(self, space_env):
        """删的是激活空间：自动改激活、写 interface、重载条目"""
        _declare_spaces(space_env["system_ref"], [DEFAULT_SPACE])
        _write_yaml(space_env["system_ref"] / f"{DEFAULT_SPACE}.yaml",
                    {"version": 1, "references": [_entry("A.png", "甲")]})
        _write_yaml(space_env["system_ref"] / "空间A.yaml",
                    {"version": 1, "references": [_entry("B.png", "乙")]})
        db = ReferenceDatabase(dev_mode=True)
        db.set_active_space("空间A")
        assert [e.label for e in db.entries] == ["乙"]

        assert db.delete_space("空间A") is True
        assert db.get_active_space() == DEFAULT_SPACE
        assert [e.label for e in db.entries] == ["甲"]  # 已重载到新激活空间
        data = json.loads(space_env["interface"].read_text(encoding="utf-8"))
        assert data["actives"]["space"] == DEFAULT_SPACE
        assert "active_space" not in data

    def test_deleting_inactive_space_keeps_active(self, space_env):
        """删非激活空间：激活空间与已加载条目不受影响"""
        _write_yaml(space_env["system_ref"] / f"{DEFAULT_SPACE}.yaml",
                    {"version": 1, "references": [_entry("A.png", "甲")]})
        _declare_spaces(space_env["system_ref"], ["空间A"])
        db = ReferenceDatabase(dev_mode=True)
        assert db.get_active_space() == DEFAULT_SPACE
        assert db.delete_space("空间A") is True
        assert db.get_active_space() == DEFAULT_SPACE
        assert [e.label for e in db.entries] == ["甲"]
