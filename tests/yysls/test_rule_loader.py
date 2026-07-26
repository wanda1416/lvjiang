"""调律规则加载器测试

覆盖 TuningRuleManager 的加载/排序/校验拒绝/保存与 get_raw 深拷贝、
create_rule/delete_rule，以及规则内词条名与标准词条全集
（attributes.yaml 普通词组 _aliases）的一致性守护。
"""

from pathlib import Path

import pytest
import yaml

from src.apps.yysls.evaluator import get_school_rules
from src.apps.yysls.evaluator.rules import (
    PVP_NAMES, RuleValidationError, TuningRuleManager,
    get_tuning_rule_manager, standard_affix_names,
)


def minimal_rule(**overrides) -> dict:
    """构造一份最小合法规则 dict（测试按需覆盖字段制造非法样本）"""
    data = {
        "key": "t1",
        "name": "测试规则",
        "affix_pool": ["最大外功攻击", "劲"],
        "patterns": {
            "环": {
                "first": ["最大外功攻击"],
                "required": [["最大外功攻击"], ["劲"]],
                "optional_n": 2,
            },
        },
    }
    data.update(overrides)
    return data


def write_rule(tmp_path: Path, data: dict, name: str = "t1.yaml") -> Path:
    path = tmp_path / name
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False)
    return path


# ─── 内置规则加载 ──────────────────────────────────────────

class TestBuiltinRules:
    def test_all_loaded_without_errors(self):
        mgr = get_tuning_rule_manager()
        assert mgr.errors == {}
        assert list(mgr.get_rules()) == [
            "huiyi_general", "huixin_small", "huixin_big",
            "heal_pure", "heal_fire",
        ]

    def test_order_ascending(self):
        rules = get_school_rules()
        orders = [r.order for r in rules.values()]
        assert orders == sorted(orders)

    def test_required_fields_present(self):
        for rule in get_school_rules().values():
            assert rule.key and rule.name
            assert rule.affix_pool
            assert rule.patterns
            for pattern in rule.patterns.values():
                assert pattern.first
                assert len(pattern.required) + pattern.optional_n == 4


# ─── schema 校验拒绝 ───────────────────────────────────────

class TestValidation:
    def test_minimal_rule_valid(self, tmp_path):
        write_rule(tmp_path, minimal_rule())
        mgr = TuningRuleManager(rules_dir=tmp_path)
        assert mgr.errors == {}
        assert list(mgr.get_rules()) == ["t1"]

    def test_empty_skeleton_valid(self, tmp_path):
        """空 affix_pool/patterns 的骨架规则可保存（新建规则）"""
        write_rule(tmp_path, minimal_rule(affix_pool=[], patterns={}))
        mgr = TuningRuleManager(rules_dir=tmp_path)
        assert mgr.errors == {}
        rule = mgr.get_rule("t1")
        assert rule.affix_pool == [] and rule.patterns == {}

    @pytest.mark.parametrize("mutate", [
        # 缺少必填字段 key/name
        lambda d: d.pop("key"),
        # 旧版 variants 结构不再支持
        lambda d: d.update(variants={"default": {}}),
        # affix_pool 词条不在标准词条全集
        lambda d: d["affix_pool"].append("大外"),
        # 槽数不满足 必选+可选+首 == 5
        lambda d: d["patterns"]["环"].update(optional_n=3),
        # not_together 须恰好 2 个词条
        lambda d: d["patterns"]["环"].update(
            top=[{"not_together": ["最大外功攻击", "劲", "敏"]}]),
        # 未知条件原语
        lambda d: d["patterns"]["环"].update(
            top=[{"unknown_kind": ["最大外功攻击"]}]),
        # 未知部位 key
        lambda d: d["patterns"].update(
            鞋子={"first": ["最大外功攻击"], "required": [["劲"]],
                  "optional_n": 3}),
        # weapons 引用未定义的子流派
        lambda d: d.update(weapons={"nosub": {"main": {"剑": "剑武学增伤"}}}),
        # own_attr 非法
        lambda d: d.update(own_attr="外功"),
        # optional_pool 超出 affix_pool
        lambda d: d.update(optional_pool=["敏"]),
    ])
    def test_invalid_rule_rejected(self, tmp_path, mutate):
        data = minimal_rule()
        mutate(data)
        write_rule(tmp_path, data)
        mgr = TuningRuleManager(rules_dir=tmp_path)
        assert mgr.get_rules() == {}
        assert mgr.errors  # 错误被记录（文件 stem → 错误信息）

    def test_bad_file_skipped_others_loaded(self, tmp_path):
        write_rule(tmp_path, minimal_rule())
        bad = minimal_rule(key="t2", affix_pool=["大外"])
        write_rule(tmp_path, bad, name="t2.yaml")
        mgr = TuningRuleManager(rules_dir=tmp_path)
        assert list(mgr.get_rules()) == ["t1"]
        assert "t2" in mgr.errors

    def test_validate_returns_message(self, tmp_path):
        mgr = TuningRuleManager(rules_dir=tmp_path)
        assert mgr.validate(minimal_rule()) is None
        msg = mgr.validate(minimal_rule(affix_pool=["神速"]))
        assert msg and "神速" in msg


# ─── 保存与 get_raw ────────────────────────────────────────

class TestSaveAndRaw:
    def test_save_rule_reloads(self, tmp_path):
        write_rule(tmp_path, minimal_rule())
        mgr = TuningRuleManager(rules_dir=tmp_path)
        data = mgr.get_raw("t1")
        data["name"] = "改名规则"
        mgr.save_rule("t1", data)
        assert mgr.get_rule("t1").name == "改名规则"

    def test_save_invalid_raises_and_keeps_file(self, tmp_path):
        write_rule(tmp_path, minimal_rule())
        mgr = TuningRuleManager(rules_dir=tmp_path)
        bad = mgr.get_raw("t1")
        bad["affix_pool"] = ["神速"]
        with pytest.raises(RuleValidationError):
            mgr.save_rule("t1", bad)
        assert mgr.get_rule("t1").name == "测试规则"  # 原文件未被破坏

    def test_get_raw_is_deepcopy(self, tmp_path):
        write_rule(tmp_path, minimal_rule())
        mgr = TuningRuleManager(rules_dir=tmp_path)
        raw = mgr.get_raw("t1")
        raw["affix_pool"].append("神速")
        assert "神速" not in mgr.get_raw("t1")["affix_pool"]


# ─── 创建与删除 ────────────────────────────────────────────

class TestCreateAndDelete:
    def test_create_rule(self, tmp_path):
        mgr = TuningRuleManager(rules_dir=tmp_path)
        mgr.create_rule("new_rule", "新规则")
        assert (tmp_path / "new_rule.yaml").exists()
        rule = mgr.get_rule("new_rule")
        assert rule.name == "新规则"
        assert rule.affix_pool == [] and rule.patterns == {}

    @pytest.mark.parametrize("key,name", [
        ("New", "非法大写"),
        ("1abc", "数字开头"),
        ("中文", "非英文"),
        ("valid_key", ""),
    ])
    def test_create_rule_invalid_inputs(self, tmp_path, key, name):
        mgr = TuningRuleManager(rules_dir=tmp_path)
        with pytest.raises(RuleValidationError):
            mgr.create_rule(key, name)

    def test_create_duplicate_key_rejected(self, tmp_path):
        write_rule(tmp_path, minimal_rule())
        mgr = TuningRuleManager(rules_dir=tmp_path)
        with pytest.raises(RuleValidationError):
            mgr.create_rule("t1", "重复")

    def test_delete_rule(self, tmp_path):
        write_rule(tmp_path, minimal_rule())
        mgr = TuningRuleManager(rules_dir=tmp_path)
        mgr.delete_rule("t1")
        assert not (tmp_path / "t1.yaml").exists()
        assert mgr.get_rules() == {}

    def test_delete_unknown_raises(self, tmp_path):
        mgr = TuningRuleManager(rules_dir=tmp_path)
        with pytest.raises(RuleValidationError):
            mgr.delete_rule("nope")


# ─── 标准词条名守护 ────────────────────────────────────────

class TestStandardAffixNames:
    def test_rule_affix_names_are_standard(self):
        # 规则内出现的词条名（DMG 占位符除外）必须在标准词条全集内，
        # 否则判定时与 OCR 解析结果对不上
        standard = set(standard_affix_names())
        for rule in get_school_rules().values():
            used: set[str] = set()
            used.update(rule.transmute_priority)
            used.update(rule.affix_pool)
            used.update(rule.optional_pool or [])
            for entry in rule.weapons.values():
                used.update(entry.main.values())
            for pattern in rule.patterns.values():
                used.update(pattern.first)
                for slot in pattern.required:
                    used.update(c for c in slot if c != "DMG")
                if pattern.required_damage not in (None, "DMG"):
                    used.add(pattern.required_damage)
                if pattern.damage_pvp_substitute:
                    used.add(pattern.damage_pvp_substitute)
                used.update(pattern.allowed_divine_pvp)
            unknown = used - standard
            assert not unknown, f"{rule.key} 存在非标准词条名: {unknown}"

    def test_pvp_names_are_standard(self):
        assert PVP_NAMES <= set(standard_affix_names())
