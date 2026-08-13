"""ConfigResolver 单测：模式判定 / 实体影子墓碑 / 聚合 diff 深合并 / 失效通知

全部在 tmp_path 上构造隔离实例，不触碰真实 config 目录。
"""

import pytest
import yaml

from lvjiang.core.config_resolver import (
    ConfigResolver,
    compute_diff,
    merge_doc,
)


@pytest.fixture
def dirs(tmp_path):
    system = tmp_path / "system"
    local = tmp_path / "local"
    system.mkdir()
    local.mkdir()
    return system, local


def _dev(dirs) -> ConfigResolver:
    return ConfigResolver(system_dir=dirs[0], local_dir=dirs[1], dev_mode=True)


def _user(dirs) -> ConfigResolver:
    return ConfigResolver(system_dir=dirs[0], local_dir=dirs[1], dev_mode=False)


# ─── 模式判定 ────────────────────────────────────────────

class TestModeDetection:
    def test_env_forces_dev(self, monkeypatch, tmp_path):
        import lvjiang.constants as constants
        monkeypatch.setattr(constants, "PROJECT_ROOT", tmp_path)  # 无 .git
        monkeypatch.setenv("LVJIANG_DEV_MODE", "1")
        assert ConfigResolver().is_dev_mode() is True

    def test_env_forces_user(self, monkeypatch, tmp_path):
        import lvjiang.constants as constants
        (tmp_path / ".git").mkdir()
        monkeypatch.setattr(constants, "PROJECT_ROOT", tmp_path)
        monkeypatch.setenv("LVJIANG_DEV_MODE", "0")
        assert ConfigResolver().is_dev_mode() is False

    def test_git_probe(self, monkeypatch, tmp_path):
        import lvjiang.constants as constants
        monkeypatch.delenv("LVJIANG_DEV_MODE", raising=False)
        monkeypatch.setattr(constants, "PROJECT_ROOT", tmp_path)
        assert ConfigResolver().is_dev_mode() is False
        (tmp_path / ".git").mkdir()
        assert ConfigResolver().is_dev_mode() is True

    def test_explicit_override_wins(self, monkeypatch):
        monkeypatch.setenv("LVJIANG_DEV_MODE", "1")
        assert ConfigResolver(dev_mode=False).is_dev_mode() is False


# ─── 实体文件：影子 / 枚举 / 墓碑 ─────────────────────────

class TestEntity:
    def test_local_shadow_overrides_system(self, dirs):
        system, local = dirs
        (system / "scenes").mkdir()
        (system / "scenes" / "a.yaml").write_text("layer: system", encoding="utf-8")
        (local / "scenes").mkdir()
        (local / "scenes" / "a.yaml").write_text("layer: local", encoding="utf-8")
        r = _user(dirs)
        assert r.resolve_read("scenes/a.yaml") == local / "scenes" / "a.yaml"

    def test_resolve_read_falls_back_to_system(self, dirs):
        system, _ = dirs
        (system / "scenes").mkdir()
        (system / "scenes" / "a.yaml").write_text("x", encoding="utf-8")
        r = _user(dirs)
        assert r.resolve_read("scenes/a.yaml") == system / "scenes" / "a.yaml"
        assert r.resolve_read("scenes/missing.yaml") is None

    def test_tombstone_hides_system_file(self, dirs):
        system, local = dirs
        (system / "scenes").mkdir()
        (system / "scenes" / "a.yaml").write_text("x", encoding="utf-8")
        (local / "scenes").mkdir()
        (local / "scenes" / "a.yaml.deleted").touch()
        r = _user(dirs)
        assert r.resolve_read("scenes/a.yaml") is None

    def test_enumerate_union_and_skip_underscore(self, dirs):
        system, local = dirs
        (system / "workflows").mkdir()
        (system / "workflows" / "a.wf").write_text("", encoding="utf-8")
        (system / "workflows" / "_draft.wf").write_text("", encoding="utf-8")
        (local / "workflows").mkdir()
        (local / "workflows" / "a.wf").write_text("", encoding="utf-8")  # 同名遮盖
        (local / "workflows" / "b.wf").write_text("", encoding="utf-8")
        r = _user(dirs)
        assert r.enumerate_entities("workflows", "*.wf") == ["a.wf", "b.wf"]

    def test_enumerate_excludes_tombstoned(self, dirs):
        system, local = dirs
        (system / "workflows").mkdir()
        (system / "workflows" / "a.wf").write_text("", encoding="utf-8")
        (system / "workflows" / "b.wf").write_text("", encoding="utf-8")
        (local / "workflows").mkdir()
        (local / "workflows" / "b.wf.deleted").touch()
        r = _user(dirs)
        assert r.enumerate_entities("workflows", "*.wf") == ["a.wf"]

    def test_write_entity_routes_by_mode(self, dirs):
        system, local = dirs
        _dev(dirs).write_entity("layouts/x.json", "{}")
        assert (system / "layouts" / "x.json").exists()
        _user(dirs).write_entity("layouts/y.json", "{}")
        assert (local / "layouts" / "y.json").exists()
        assert not (system / "layouts" / "y.json").exists()

    def test_write_entity_clears_tombstone(self, dirs):
        system, local = dirs
        (local / "layouts").mkdir()
        tomb = local / "layouts" / "x.json.deleted"
        tomb.touch()
        _user(dirs).write_entity("layouts/x.json", "{}")
        assert not tomb.exists()
        assert (local / "layouts" / "x.json").exists()

    def test_write_entity_bytes(self, dirs):
        _, local = dirs
        target = _user(dirs).write_entity("references/g/a.png", b"\x89PNG")
        assert target == local / "references" / "g" / "a.png"
        assert target.read_bytes() == b"\x89PNG"

    def test_delete_entity_dev_removes_system(self, dirs):
        system, local = dirs
        (system / "scenes").mkdir()
        (system / "scenes" / "a.yaml").write_text("x", encoding="utf-8")
        _dev(dirs).delete_entity("scenes/a.yaml")
        assert not (system / "scenes" / "a.yaml").exists()
        assert not (local / "scenes" / "a.yaml.deleted").exists()

    def test_delete_entity_user_tombstones_system(self, dirs):
        system, local = dirs
        (system / "scenes").mkdir()
        (system / "scenes" / "a.yaml").write_text("x", encoding="utf-8")
        r = _user(dirs)
        r.delete_entity("scenes/a.yaml")
        assert (system / "scenes" / "a.yaml").exists()  # system 不动
        assert (local / "scenes" / "a.yaml.deleted").exists()
        assert r.resolve_read("scenes/a.yaml") is None

    def test_delete_entity_user_local_only_no_tombstone(self, dirs):
        _, local = dirs
        (local / "scenes").mkdir()
        (local / "scenes" / "mine.yaml").write_text("x", encoding="utf-8")
        _user(dirs).delete_entity("scenes/mine.yaml")
        assert not (local / "scenes" / "mine.yaml").exists()
        # system 无同名 → 不落墓碑
        assert not (local / "scenes" / "mine.yaml.deleted").exists()


# ─── 聚合：合并与 diff 纯函数 ─────────────────────────────

class TestMergeAndDiff:
    def test_dict_deep_merge(self):
        base = {"a": {"x": 1, "y": 2}, "b": 1}
        overlay = {"a": {"y": 20, "z": 30}}
        assert merge_doc(base, overlay) == {"a": {"x": 1, "y": 20, "z": 30}, "b": 1}

    def test_list_and_scalar_replace_whole_key(self):
        base = {"lst": [1, 2, 3], "s": "old"}
        overlay = {"lst": [9], "s": "new"}
        assert merge_doc(base, overlay) == {"lst": [9], "s": "new"}

    def test_deleted_key_removes(self):
        base = {"a": 1, "b": {"c": 2, "d": 3}}
        overlay = {"__deleted__": ["a"], "b": {"__deleted__": ["c"]}}
        assert merge_doc(base, overlay) == {"b": {"d": 3}}

    def test_merge_does_not_mutate_inputs(self):
        base = {"a": {"x": 1}}
        overlay = {"a": {"x": 2}}
        merge_doc(base, overlay)
        assert base == {"a": {"x": 1}}

    @pytest.mark.parametrize("desired", [
        {},
        {"a": 1},
        {"a": {"b": {"c": [1, 2]}}, "d": "s"},
        {"lst": [3, 2, 1]},
        {"a": {"kept": 1}},
    ])
    def test_roundtrip_identity(self, desired):
        base = {"a": {"b": {"c": [9]}, "kept": 1, "gone": 2}, "top": True}
        diff = compute_diff(base, desired)
        assert merge_doc(base, diff) == desired

    def test_diff_empty_when_equal(self):
        base = {"a": {"b": 1}, "c": [1, 2]}
        assert compute_diff(base, base) == {}


# ─── 聚合：load_merged / save_merged ─────────────────────

class TestAggregateIO:
    def _write_system(self, dirs, doc):
        (dirs[0] / "scenes.yaml").write_text(
            yaml.dump(doc, allow_unicode=True), encoding="utf-8")

    def test_load_merged_without_overlay(self, dirs):
        self._write_system(dirs, {"a": 1})
        assert _user(dirs).load_merged("scenes.yaml") == {"a": 1}

    def test_save_merged_dev_writes_full_system(self, dirs):
        system, local = dirs
        self._write_system(dirs, {"a": 1})
        _dev(dirs).save_merged("scenes.yaml", {"a": 2, "b": 3})
        on_disk = yaml.safe_load((system / "scenes.yaml").read_text(encoding="utf-8"))
        assert on_disk == {"a": 2, "b": 3}
        assert not (local / "scenes.yaml").exists()

    def test_save_merged_user_writes_min_diff(self, dirs):
        system, local = dirs
        self._write_system(dirs, {"a": 1, "b": {"c": 2, "d": 3}})
        r = _user(dirs)
        r.save_merged("scenes.yaml", {"a": 1, "b": {"c": 20}})
        overlay = yaml.safe_load((local / "scenes.yaml").read_text(encoding="utf-8"))
        assert overlay == {"b": {"__deleted__": ["d"], "c": 20}}
        # system 原样
        assert yaml.safe_load((system / "scenes.yaml").read_text(
            encoding="utf-8")) == {"a": 1, "b": {"c": 2, "d": 3}}
        # 合并视图回读一致
        assert r.load_merged("scenes.yaml") == {"a": 1, "b": {"c": 20}}

    def test_save_merged_user_empty_diff_deletes_overlay(self, dirs):
        _, local = dirs
        self._write_system(dirs, {"a": 1})
        (local / "scenes.yaml").write_text("a: 99", encoding="utf-8")
        _user(dirs).save_merged("scenes.yaml", {"a": 1})  # 回到 system 原值
        assert not (local / "scenes.yaml").exists()


# ─── 失效通知 ────────────────────────────────────────────

class TestChangeNotify:
    def test_listener_receives_rel_path(self, dirs):
        r = _dev(dirs)
        got = []
        r.add_change_listener(got.append)
        r.write_entity("scenes/a.yaml", "x")
        r.delete_entity("scenes/a.yaml")
        r.save_merged("scenes.yaml", {"a": 1})
        assert got == ["scenes/a.yaml", "scenes/a.yaml", "scenes.yaml"]

    def test_listener_exception_does_not_block(self, dirs):
        r = _dev(dirs)
        got = []

        def bad(_):
            raise RuntimeError("boom")

        r.add_change_listener(bad)
        r.add_change_listener(got.append)
        r.write_entity("scenes/a.yaml", "x")
        assert got == ["scenes/a.yaml"]

    def test_remove_listener(self, dirs):
        r = _dev(dirs)
        got = []
        r.add_change_listener(got.append)
        r.remove_change_listener(got.append)
        r.write_entity("scenes/a.yaml", "x")
        assert got == []
