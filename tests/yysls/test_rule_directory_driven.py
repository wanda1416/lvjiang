"""规则与基础规则组的存在性由目录决定、顺序与启停写在文件里；
tune_config 只剩品阶门槛与开关；开发者可把规则组放进 local。"""

import shutil
from pathlib import Path

import pytest
import yaml

from lvjiang.apps.yysls.core.tuning_rules import (
    RuleValidationError,
    TuneConfigManager,
    TuningGroupManager,
    TuningRuleManager,
)
from lvjiang.core.config.edit_session import ConfigEditSession
from lvjiang.core.config.resolver import ConfigResolver, SystemContentProtected

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SYSTEM_YYSLS = PROJECT_ROOT / "config" / "system" / "yysls"


def _layered(tmp_path, *, dev_mode: bool) -> ConfigResolver:
    """把真实 yysls 配置复制成 system 层，local 空，构造两层 resolver。"""
    system = tmp_path / "system"
    shutil.copytree(SYSTEM_YYSLS, system / "yysls")
    return ConfigResolver(system_dir=system, local_dir=tmp_path / "local", dev_mode=dev_mode)


def _read(path: Path) -> dict:
    return yaml.safe_load(path.read_text("utf-8")) or {}


class TestRulesFromDirectory:
    def test_order_and_disabled_come_from_rule_files(self, tmp_path):
        resolver = _layered(tmp_path, dev_mode=True)
        rules_dir = tmp_path / "system/yysls/tuning_rules"
        # 手写一条 order 15 的规则应插到 10 与 20 之间；再把 heal_fire 标为禁用
        mid = _read(rules_dir / "huixin_big.yaml")
        mid.update(key="mid_rule", name="中间规则", order=15)
        (rules_dir / "mid_rule.yaml").write_text(yaml.safe_dump(mid, allow_unicode=True), "utf-8")
        fire = _read(rules_dir / "heal_fire.yaml")
        fire["disabled"] = True
        (rules_dir / "heal_fire.yaml").write_text(yaml.safe_dump(fire, allow_unicode=True), "utf-8")

        manager = TuningRuleManager(resolver=resolver)

        keys = list(manager.get_rules())
        assert keys[:3] == ["huiyi_general", "mid_rule", "huixin_small"]
        assert "heal_fire" not in keys
        assert not manager.is_rule_enabled("heal_fire")
        assert manager.is_rule_enabled("huiyi_general")
        # 导航仍包含禁用规则，且按 order 排
        nav = [k for k, _ in manager.get_all_rule_keys_and_names()]
        assert nav.index("heal_fire") > nav.index("heal_pure")

    def test_set_rule_enabled_writes_rule_file_not_tune_config(self, tmp_path):
        resolver = _layered(tmp_path, dev_mode=True)
        manager = TuningRuleManager(resolver=resolver)

        manager.set_rule_enabled("huixin_big", False)

        assert _read(tmp_path / "system/yysls/tuning_rules/huixin_big.yaml")["disabled"] is True
        assert "tuning_rules" not in _read(tmp_path / "system/yysls/tune_config.yaml")
        assert "huixin_big" not in manager.get_rules()

        manager.set_rule_enabled("huixin_big", True)
        assert "disabled" not in _read(tmp_path / "system/yysls/tuning_rules/huixin_big.yaml")
        assert "huixin_big" in manager.get_rules()

    def test_user_mode_disable_writes_a_stub_and_enable_removes_it(self, tmp_path):
        """没改过的系统规则：停用只在 local 放一个 key + disabled 的桩，正文仍来自
        system；重新启用 = 删桩。用户一眼就知道这个 local 文件可以直接删。"""
        resolver = _layered(tmp_path, dev_mode=False)
        manager = TuningRuleManager(resolver=resolver)

        manager.set_rule_enabled("huixin_big", False)

        stub = tmp_path / "local/yysls/tuning_rules/huixin_big.yaml"
        assert _read(stub) == {"key": "huixin_big", "disabled": True}
        assert "huixin_big" not in manager.get_rules()
        assert manager.is_rule_local_stub("huixin_big")
        # 导航仍能拿到规则名（正文来自 system）
        assert dict(manager.get_all_rule_keys_and_names())["huixin_big"] == "会心大外"
        # 来源展示的是生效的正文（系统），不是桩
        assert manager.describe_rule_version("huixin_big").layer == "system"

        # 系统正文更新后，停用中的规则跟着变（桩不冻结内容）
        system_file = tmp_path / "system/yysls/tuning_rules/huixin_big.yaml"
        data = _read(system_file)
        data["name"] = "会心大外 v2"
        system_file.write_text(yaml.safe_dump(data, allow_unicode=True), "utf-8")
        manager.reload()
        assert dict(manager.get_all_rule_keys_and_names())["huixin_big"] == "会心大外 v2"

        manager.set_rule_enabled("huixin_big", True)
        assert not stub.exists()
        assert "huixin_big" in manager.get_rules()
        assert not manager.is_rule_local_stub("huixin_big")

    def test_user_mode_disable_keeps_full_shadow_when_rule_was_edited(self, tmp_path):
        resolver = _layered(tmp_path, dev_mode=False)
        manager = TuningRuleManager(resolver=resolver)
        raw = manager.get_raw("huixin_big")
        raw["name"] = "我改过的大外"
        manager.save_rule("huixin_big", raw)             # 完整 local 影子

        manager.set_rule_enabled("huixin_big", False)

        shadow = _read(tmp_path / "local/yysls/tuning_rules/huixin_big.yaml")
        assert shadow["disabled"] is True and shadow["name"] == "我改过的大外"
        assert not manager.is_rule_local_stub("huixin_big")

        manager.set_rule_enabled("huixin_big", True)
        shadow = _read(tmp_path / "local/yysls/tuning_rules/huixin_big.yaml")
        assert "disabled" not in shadow and shadow["name"] == "我改过的大外"

    def test_deleting_the_stub_by_hand_re_enables(self, tmp_path):
        resolver = _layered(tmp_path, dev_mode=False)
        manager = TuningRuleManager(resolver=resolver)
        manager.set_rule_enabled("heal_pure", False)
        (tmp_path / "local/yysls/tuning_rules/heal_pure.yaml").unlink()

        manager.reload()

        assert "heal_pure" in manager.get_rules()

    def test_create_rule_writes_default_order_and_no_declaration(self, tmp_path):
        resolver = _layered(tmp_path, dev_mode=True)
        manager = TuningRuleManager(resolver=resolver)

        manager.create_rule("my_rule", "我的规则")

        data = _read(tmp_path / "system/yysls/tuning_rules/my_rule.yaml")
        assert data["order"] == 10 and "disabled" not in data
        assert "tuning_rules" not in _read(tmp_path / "system/yysls/tune_config.yaml")
        assert "my_rule" in manager.get_rules()


class TestLegacyMigration:
    def test_legacy_tuning_rules_declaration_migrates_to_files(self, tmp_path):
        resolver = _layered(tmp_path, dev_mode=True)
        config_path = tmp_path / "system/yysls/tune_config.yaml"
        data = _read(config_path)
        data["base_rules"] = ["default", "aggressive"]
        data["tuning_rules"] = {"huiyi_general": True, "heal_pure": False}
        config_path.write_text(yaml.safe_dump(data, allow_unicode=True), "utf-8")

        manager = TuningRuleManager(resolver=resolver)

        migrated = _read(config_path)
        assert "tuning_rules" not in migrated and "base_rules" not in migrated
        assert _read(tmp_path / "system/yysls/tuning_rules/heal_pure.yaml")["disabled"] is True
        assert "heal_pure" not in manager.get_rules()
        assert TuneConfigManager(resolver=resolver).get().switches

    def test_legacy_user_diff_migrates_to_local_shadow(self, tmp_path):
        resolver = _layered(tmp_path, dev_mode=False)
        local_cfg = tmp_path / "local/yysls/tune_config.yaml"
        local_cfg.parent.mkdir(parents=True)
        local_cfg.write_text(yaml.safe_dump({"tuning_rules": {"heal_pure": False}}), "utf-8")

        manager = TuningRuleManager(resolver=resolver)

        assert "heal_pure" not in manager.get_rules()
        # 用户模式迁移写的是桩，不是整份影子
        assert _read(tmp_path / "local/yysls/tuning_rules/heal_pure.yaml") == {
            "key": "heal_pure", "disabled": True}
        # local diff 里的旧声明被清掉；system 文件原样
        assert "tuning_rules" not in (_read(local_cfg) if local_cfg.exists() else {})
        assert "tuning_rules" not in _read(tmp_path / "system/yysls/tune_config.yaml")


class TestGroupsFromDirectory:
    def test_dialog_session_preserves_selected_local_group_layer(self, tmp_path):
        resolver = _layered(tmp_path, dev_mode=True)
        session = ConfigEditSession(
            resolver, merged_paths=("yysls/tune_config.yaml",),
            entity_dirs=("yysls/base_groups", "yysls/tuning_rules"))
        manager = TuningGroupManager(resolver=session.resolver)
        manager.create_group("mine", "本地组", layer="local")

        session.commit()
        rel_path = "yysls/base_groups/mine.yaml"
        assert (tmp_path / "local" / rel_path).is_file()
        assert not (tmp_path / "system" / rel_path).exists()
        assert resolver.describe_entity(rel_path).layer == "local"

        manager.move_group("mine", "system")
        session.commit()
        assert not (tmp_path / "local" / rel_path).exists()
        assert (tmp_path / "system" / rel_path).is_file()
        session.close()

    def test_groups_enumerated_and_ordered(self, tmp_path):
        resolver = _layered(tmp_path, dev_mode=True)
        groups_dir = tmp_path / "system/yysls/base_groups"
        extra = _read(groups_dir / "default.yaml")
        extra.update(key="early", name="靠前", order=5)
        (groups_dir / "early.yaml").write_text(yaml.safe_dump(extra, allow_unicode=True), "utf-8")

        manager = TuningGroupManager(resolver=resolver)

        assert list(manager.get_groups()) == ["early", "default", "aggressive"]
        assert [g.order for g in manager.get_groups().values()] == [5, 10, 20]

    def test_key_must_match_filename(self, tmp_path):
        resolver = _layered(tmp_path, dev_mode=True)
        groups_dir = tmp_path / "system/yysls/base_groups"
        bad = _read(groups_dir / "default.yaml")
        bad["key"] = "other"
        (groups_dir / "mismatch.yaml").write_text(yaml.safe_dump(bad, allow_unicode=True), "utf-8")

        manager = TuningGroupManager(resolver=resolver)

        assert "mismatch" in manager.errors and "other" not in manager.get_groups()

    def test_developer_can_keep_a_group_in_local(self, tmp_path):
        resolver = _layered(tmp_path, dev_mode=True)
        manager = TuningGroupManager(resolver=resolver)
        assert manager.can_choose_layer()

        manager.create_group("mine", "我的私有组", layer="local")

        assert (tmp_path / "local/yysls/base_groups/mine.yaml").exists()
        assert not (tmp_path / "system/yysls/base_groups/mine.yaml").exists()
        assert manager.layer_of("mine") == "local"
        assert manager.layer_of("default") == "system"
        assert "base_rules" not in _read(tmp_path / "system/yysls/tune_config.yaml")

        # 保存写回 local，不会被“保存”悄悄搬进 system
        raw = manager.get_raw("mine")
        raw["description"] = "改一下"
        manager.save_group("mine", raw)
        assert not (tmp_path / "system/yysls/base_groups/mine.yaml").exists()
        assert _read(tmp_path / "local/yysls/base_groups/mine.yaml")["description"] == "改一下"

        # 搬到 system，再搬回来
        manager.move_group("mine", "system")
        assert (tmp_path / "system/yysls/base_groups/mine.yaml").exists()
        assert not (tmp_path / "local/yysls/base_groups/mine.yaml").exists()
        manager.move_group("mine", "local")
        assert manager.layer_of("mine") == "local"

        # 删除删的是文件所在层
        manager.delete_group("mine")
        assert not (tmp_path / "local/yysls/base_groups/mine.yaml").exists()
        assert "mine" not in manager.get_groups()

    def test_user_mode_cannot_choose_layer_and_writes_local(self, tmp_path):
        resolver = _layered(tmp_path, dev_mode=False)
        manager = TuningGroupManager(resolver=resolver)
        assert not manager.can_choose_layer()

        with pytest.raises(PermissionError):
            manager.create_group("nope", "x", layer="system")
        manager.create_group("mine", "我的组")
        assert (tmp_path / "local/yysls/base_groups/mine.yaml").exists()
        with pytest.raises(RuleValidationError, match="只有开发模式"):
            manager.move_group("mine", "system")
        with pytest.raises(SystemContentProtected):
            manager.delete_group("default")
