"""ConfigResolver 单测：模式判定 / 实体影子墓碑 / 聚合 diff 深合并 / 失效通知

全部在 tmp_path 上构造隔离实例，不触碰真实 config 目录。
"""

import pytest
import yaml

from lvjiang.core.config.resolver import (
    ConfigResolver,
    SystemContentProtected,
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

    def test_delete_entity_user_refuses_system(self, dirs):
        """出厂实体不允许用户删除：不落墓碑、不隐藏，直接拒绝。"""
        system, local = dirs
        (system / "scenes").mkdir()
        (system / "scenes" / "a.yaml").write_text("x", encoding="utf-8")
        r = _user(dirs)
        with pytest.raises(SystemContentProtected):
            r.delete_entity("scenes/a.yaml")
        assert (system / "scenes" / "a.yaml").exists()
        assert not (local / "scenes" / "a.yaml.deleted").exists()
        assert r.resolve_read("scenes/a.yaml") is not None

    def test_delete_entity_dev_mode_removes_system(self, dirs):
        """开发模式（自己就是 system 身份）照常可删。"""
        system, _ = dirs
        (system / "scenes").mkdir()
        (system / "scenes" / "a.yaml").write_text("x", encoding="utf-8")
        _dev(dirs).delete_entity("scenes/a.yaml")
        assert not (system / "scenes" / "a.yaml").exists()

    def test_is_system_entity(self, dirs):
        system, local = dirs
        (system / "scenes").mkdir()
        (system / "scenes" / "a.yaml").write_text("x", encoding="utf-8")
        r = _user(dirs)
        assert r.is_system_entity("scenes/a.yaml")
        assert not r.is_system_entity("scenes/mine.yaml")

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
        # b.d 未在 DELETABLE_PATHS 声明 → 不允许删除出厂内容，只记下改动的 c
        assert overlay == {"b": {"c": 20}}
        # system 原样
        assert yaml.safe_load((system / "scenes.yaml").read_text(
            encoding="utf-8")) == {"a": 1, "b": {"c": 2, "d": 3}}
        # 合并视图：改动生效，未被允许删除的 d 保留
        assert r.load_merged("scenes.yaml") == {"a": 1, "b": {"c": 20, "d": 3}}

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


# ─── 注册表列表（可增长的条目登记表） ──────────────────────

class TestRegistryList:
    """注册表列表存增量，出厂新增条目不会被 local 冻住。

    普通列表整键替换是对的（``quality_thresholds.武器: [gold]`` 这类枚举设定
    用户就是要覆盖）；但 exposed / layout_scenes.* / base_rules 是登记表，
    local 存下完整列表会导致后续版本新增的条目永远进不了合并视图。
    """

    REG = ("exposed",)

    def test_added_appends_without_freezing_base(self):
        base = {"exposed": ["a", "b"]}
        local = {"exposed": {"__added__": ["mine"]}}
        assert merge_doc(base, local, self.REG)["exposed"] == ["a", "b", "mine"]
        # 出厂后来新增 c —— 必须自动出现
        base2 = {"exposed": ["a", "b", "c"]}
        assert merge_doc(base2, local, self.REG)["exposed"] == ["a", "b", "c", "mine"]

    def test_removed_hides_base_entry(self):
        base = {"exposed": ["a", "b", "c"]}
        local = {"exposed": {"__removed__": ["b"]}}
        assert merge_doc(base, local, self.REG)["exposed"] == ["a", "c"]

    def test_removal_persists_while_new_entries_still_appear(self):
        """用户主动隐藏的保持隐藏，同时不挡住出厂新增。"""
        local = {"exposed": {"__removed__": ["b"], "__added__": ["mine"]}}
        merged = merge_doc({"exposed": ["a", "b", "c", "new"]}, local, self.REG)
        assert merged["exposed"] == ["a", "c", "new", "mine"]

    def test_order_applied_and_unknown_appended(self):
        """__order__ 没提到的条目（出厂新增）排末尾，宁可靠后也不能消失。"""
        base = {"exposed": ["a", "b", "later"]}
        local = {"exposed": {"__order__": ["b", "a"]}}
        assert merge_doc(base, local, self.REG)["exposed"] == ["b", "a", "later"]

    def test_diff_records_only_user_change(self):
        base = {"exposed": ["a", "b"]}
        diff = compute_diff(base, {"exposed": ["a", "b", "mine"]}, self.REG)
        assert diff == {"exposed": {"__added__": ["mine"]}}

    def test_diff_empty_when_unchanged(self):
        base = {"exposed": ["a", "b"]}
        assert compute_diff(base, {"exposed": ["a", "b"]}, self.REG) == {}

    def test_diff_omits_order_when_add_remove_suffices(self):
        base = {"exposed": ["a", "b"]}
        diff = compute_diff(base, {"exposed": ["a"]}, self.REG)
        assert diff == {"exposed": {"__removed__": ["b"]}}

    def test_diff_writes_order_only_when_reordered(self):
        base = {"exposed": ["a", "b", "c"]}
        diff = compute_diff(base, {"exposed": ["c", "a", "b"]}, self.REG)
        assert diff["exposed"]["__order__"] == ["c", "a", "b"]

    @pytest.mark.parametrize("desired", [
        ["a", "b", "c"],          # 不变
        ["a", "b", "c", "mine"],  # 新增
        ["a", "c"],               # 删除
        ["c", "b", "a"],          # 重排
        ["mine", "c"],            # 增删重排同时发生
        [],                       # 全部取消
    ])
    def test_roundtrip_identity(self, desired):
        base = {"exposed": ["a", "b", "c"]}
        diff = compute_diff(base, {"exposed": desired}, self.REG)
        assert merge_doc(base, diff, self.REG)["exposed"] == desired

    def test_plain_list_still_replaces(self):
        """存量 local 写死的完整列表保持原语义，不做推断。

        无法区分「用户主动删了某条」和「那条当时还不存在」，硬转会误伤；
        用户下次保存时 compute_diff 会自动转成增量形式。
        """
        base = {"exposed": ["a", "b", "c"]}
        assert merge_doc(base, {"exposed": ["a"]}, self.REG)["exposed"] == ["a"]

    def test_non_registry_list_unaffected(self):
        base = {"other": ["a", "b"]}
        assert compute_diff(base, {"other": ["a", "b", "x"]}, self.REG) == {
            "other": ["a", "b", "x"]}
        assert merge_doc(base, {"other": ["z"]}, self.REG)["other"] == ["z"]

    def test_wildcard_path_matches_one_level(self):
        reg = ("layout_scenes.*",)
        base = {"layout_scenes": {"group_1": ["s1"], "group_2": ["s2"]}}
        diff = compute_diff(
            base, {"layout_scenes": {"group_1": ["s1", "mine"], "group_2": ["s2"]}}, reg)
        assert diff == {"layout_scenes": {"group_1": {"__added__": ["mine"]}}}
        base2 = {"layout_scenes": {"group_1": ["s1", "s_new"], "group_2": ["s2"]}}
        merged = merge_doc(base2, diff, reg)
        assert merged["layout_scenes"]["group_1"] == ["s1", "s_new", "mine"]


class TestRegistryThroughResolver:
    """经 load_merged / save_merged 走完整路径，验证按文件名选用注册表声明。"""

    def _write(self, path, doc):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.dump(doc, allow_unicode=True), encoding="utf-8")

    def test_user_save_writes_increment_only(self, dirs):
        system, local = dirs
        self._write(system / "yysls" / "tune_config.yaml",
                    {"base_rules": ["a", "b"], "switches": {}})
        r = _user(dirs)
        r.save_merged("yysls/tune_config.yaml",
                      {"base_rules": ["a", "b", "mine"], "switches": {}})
        saved = yaml.safe_load(
            (local / "yysls" / "tune_config.yaml").read_text(encoding="utf-8"))
        assert saved == {"base_rules": {"__added__": ["mine"]}}

    def test_system_addition_reaches_user_after_upgrade(self, dirs):
        """本 bug 的回归点：用户存过 local 之后仍能看到出厂新增的脚本。"""
        system, local = dirs
        self._write(system / "yysls" / "tune_config.yaml",
                    {"base_rules": ["a", "b"], "switches": {}})
        r = _user(dirs)
        r.save_merged("yysls/tune_config.yaml",
                      {"base_rules": ["a", "b", "mine"], "switches": {}})
        # 发新版：出厂多了 c
        self._write(system / "yysls" / "tune_config.yaml",
                    {"base_rules": ["a", "b", "c"], "switches": {}})
        assert r.load_merged("yysls/tune_config.yaml")["base_rules"] == [
            "a", "b", "c", "mine"]

    def test_unlisted_file_keeps_replace_semantics(self, dirs):
        system, local = dirs
        self._write(system / "app.yaml", {"lst": [1, 2]})
        self._write(local / "app.yaml", {"lst": [9]})
        assert _user(dirs).load_merged("app.yaml")["lst"] == [9]


# ─── 删除白名单（默认禁止删除出厂内容） ────────────────────

class TestDeletionAllowlist:
    """出厂配置是开发者提供的初始内容，默认不允许 local 删除。

    用户该做的是改值、复制、另存为、新建；想停用某项走激活机制
    （调律规则的 tuning_rules 开关、脚本的 exposed 暴露列表），
    而不是删掉定义本身。确需支持删除的场景在 DELETABLE_PATHS 里声明。
    """

    def _write_system(self, dirs, rel, doc):
        path = dirs[0] / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.dump(doc, allow_unicode=True), encoding="utf-8")

    def test_unregistered_deletion_suppressed(self, dirs):
        self._write_system(dirs, "yysls/game_config.yaml",
                           {"schools": {"出厂流派": {"v": 1}}})
        r = _user(dirs)
        doc = r.load_merged("yysls/game_config.yaml")
        doc["schools"].pop("出厂流派")
        r.save_merged("yysls/game_config.yaml", doc)
        assert "出厂流派" in r.load_merged("yysls/game_config.yaml")["schools"]

    def test_top_level_key_never_lost_to_partial_save(self, dirs):
        """调用方只传部分文档时，其余顶层键不会被判成删除。

        save_merged 的入参语义是完整文档，但违约的代价不该是用户配置崩掉。
        """
        self._write_system(dirs, "app.yaml",
                           {"input_simulation": {"a": 1}, "feature_flags": {"x": True}})
        r = _user(dirs)
        r.save_merged("app.yaml", {"input_simulation": {"a": 9}})
        merged = r.load_merged("app.yaml")
        assert merged["feature_flags"] == {"x": True}
        assert merged["input_simulation"] == {"a": 9}

    def test_user_created_entry_freely_deletable(self, dirs):
        """用户自建的条目不在 system 基底里，删除它压根不产生 __deleted__。

        这正是「默认禁止删除」能同时满足两边的原因：挡住的只有出厂内容。
        """
        self._write_system(dirs, "yysls/game_config.yaml",
                           {"schools": {"出厂流派": {"v": 1}}})
        r = _user(dirs)
        doc = r.load_merged("yysls/game_config.yaml")
        doc["schools"]["我的流派"] = {"v": 9}
        r.save_merged("yysls/game_config.yaml", doc)
        assert "我的流派" in r.load_merged("yysls/game_config.yaml")["schools"]

        doc = r.load_merged("yysls/game_config.yaml")
        doc["schools"].pop("我的流派")
        r.save_merged("yysls/game_config.yaml", doc)
        merged = r.load_merged("yysls/game_config.yaml")
        assert "我的流派" not in merged["schools"]
        assert "出厂流派" in merged["schools"]

    def test_allowlist_is_empty_by_design(self):
        """出厂内容没有一样是该让用户删的，白名单保持空。

        保留这个扩展点是为了将来真出现例外时有地方声明，
        不是给现在留口子——空表本身就是结论。
        """
        from lvjiang.core.config.resolver import DELETABLE_PATHS
        assert DELETABLE_PATHS == {}

    def test_mechanism_works_if_ever_registered(self, dirs, monkeypatch):
        """机制本身可用：一旦声明了某路径，该路径下的删除就会生效。"""
        import lvjiang.core.config.resolver as cr
        monkeypatch.setattr(cr, "DELETABLE_PATHS", {"demo.yaml": ("items",)})
        self._write_system(dirs, "demo.yaml", {"items": {"a": 1, "b": 2}})
        r = _user(dirs)
        doc = r.load_merged("demo.yaml")
        doc["items"].pop("b")
        r.save_merged("demo.yaml", doc)
        assert r.load_merged("demo.yaml")["items"] == {"a": 1}

    def test_dev_mode_can_still_remove_keys(self, dirs):
        """开发模式全量写 system —— 开发者编排出厂配置不受白名单约束。"""
        self._write_system(dirs, "app.yaml", {"a": 1, "b": 2})
        _dev(dirs).save_merged("app.yaml", {"a": 1})
        saved = yaml.safe_load((dirs[0] / "app.yaml").read_text(encoding="utf-8"))
        assert saved == {"a": 1}

    def test_pure_function_keeps_original_semantics(self):
        """compute_diff 不传 deletable 时保持「缺键即删除」，供纯函数用法与单测。"""
        assert compute_diff({"a": 1, "b": 2}, {"a": 1}) == {"__deleted__": ["b"]}


class TestProtectedLists:
    """列表型出厂内容：不许移除，但可改值、可新增、可重排

    列表走整键替换，绕开了 __deleted__ 那条保护——用户存一份少了几项的
    列表就把出厂条目抹掉了。这里按条目身份字段比对补回。
    """

    REL = "yysls/game_config.yaml"
    BASE = {
        "weapon_types": [
            {"name": "剑", "wuxue_affix": "剑武学增伤"},
            {"name": "枪", "wuxue_affix": "枪武学增伤"},
        ],
        "level_configs": [{"level": 91}, {"level": 100}, {"level": 110}],
    }

    def _resolver(self, dirs):
        path = dirs[0] / self.REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.dump(self.BASE, allow_unicode=True), encoding="utf-8")
        return _user(dirs)

    def _roundtrip(self, r, mutate):
        doc = r.load_merged(self.REL)
        mutate(doc)
        r.save_merged(self.REL, doc)
        return r.load_merged(self.REL)

    def test_factory_entry_cannot_be_removed(self, dirs):
        r = self._resolver(dirs)
        merged = self._roundtrip(r, lambda d: d.__setitem__(
            "weapon_types", [w for w in d["weapon_types"] if w["name"] != "剑"]))
        assert [w["name"] for w in merged["weapon_types"]] == ["剑", "枪"]

    def test_factory_entry_value_change_kept(self, dirs):
        r = self._resolver(dirs)
        merged = self._roundtrip(
            r, lambda d: d["weapon_types"][0].__setitem__("wuxue_affix", "我改的"))
        assert merged["weapon_types"][0]["wuxue_affix"] == "我改的"

    def test_user_added_entry_kept(self, dirs):
        r = self._resolver(dirs)
        merged = self._roundtrip(r, lambda d: d["weapon_types"].append(
            {"name": "我的武器", "wuxue_affix": "x"}))
        assert [w["name"] for w in merged["weapon_types"]] == ["剑", "枪", "我的武器"]

    def test_user_added_entry_still_deletable(self, dirs):
        """用户自己加的条目不受保护，加完能删掉。"""
        r = self._resolver(dirs)
        self._roundtrip(r, lambda d: d["weapon_types"].append({"name": "我的武器"}))
        merged = self._roundtrip(r, lambda d: d.__setitem__(
            "weapon_types", [w for w in d["weapon_types"] if w["name"] != "我的武器"]))
        assert [w["name"] for w in merged["weapon_types"]] == ["剑", "枪"]

    def test_missing_entry_restored_near_original_position(self, dirs):
        r = self._resolver(dirs)
        merged = self._roundtrip(r, lambda d: d.__setitem__(
            "level_configs", [c for c in d["level_configs"] if c["level"] != 100]))
        assert [c["level"] for c in merged["level_configs"]] == [91, 100, 110]

    def test_user_reorder_respected(self, dirs):
        r = self._resolver(dirs)
        merged = self._roundtrip(r, lambda d: d.__setitem__(
            "level_configs",
            [{"level": 110}, {"level": 91}, {"level": 100}, {"level": 120}]))
        assert [c["level"] for c in merged["level_configs"]] == [110, 91, 100, 120]

    def test_dev_mode_can_remove(self, dirs):
        """开发模式全量写 system，编排出厂内容不受保护。"""
        path = dirs[0] / self.REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.dump(self.BASE, allow_unicode=True), encoding="utf-8")
        r = _dev(dirs)
        doc = r.load_merged(self.REL)
        doc["weapon_types"] = [w for w in doc["weapon_types"] if w["name"] != "剑"]
        r.save_merged(self.REL, doc)
        assert [w["name"] for w in r.load_merged(self.REL)["weapon_types"]] == ["枪"]

    def test_unprotected_list_still_replaced(self, dirs):
        r = self._resolver(dirs)
        merged = self._roundtrip(r, lambda d: d.__setitem__("其他列表", [1]))
        assert merged["其他列表"] == [1]
