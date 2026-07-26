"""调律规则加载器测试

覆盖 TuningRuleManager 的加载/排序/校验拒绝/保存与 get_raw 深拷贝，
以及规则内全称词条与 attributes.yaml 标准字段名的一致性守护。
"""

from pathlib import Path

import pytest
import yaml

from src.apps.yysls.evaluator import get_school_rules
from src.apps.yysls.evaluator.rules import (
    PVP_NAMES, SYMBOL_VOCAB, RuleValidationError, TuningRuleManager,
    get_tuning_rule_manager,
)

PROJECT_ROOT = Path(__file__).parents[2]


def minimal_rule(**overrides) -> dict:
    """构造一份最小合法规则 dict（测试按需覆盖字段制造非法样本）"""
    data = {
        "key": "t1",
        "name": "测试流派",
        "variants": {
            "default": {
                "affix_pool": ["大外", "劲"],
                "patterns": {
                    "环": {
                        "first": ["大外"],
                        "required": [["大外"], ["劲"]],
                        "optional_n": 2,
                    },
                },
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
            "huiyi_general", "huixin_small", "huixin_big", "heal",
        ]

    def test_order_ascending(self):
        rules = get_school_rules()
        orders = [r.order for r in rules.values()]
        assert orders == sorted(orders)

    def test_required_fields_present(self):
        for rule in get_school_rules().values():
            assert rule.key and rule.name
            assert rule.variants
            for variant in rule.variants.values():
                assert variant.affix_pool
                assert variant.patterns
                for pattern in variant.patterns.values():
                    assert pattern.first
                    assert len(pattern.required) + pattern.optional_n == 4


# ─── schema 校验拒绝 ───────────────────────────────────────

class TestValidation:
    def test_minimal_rule_valid(self, tmp_path):
        write_rule(tmp_path, minimal_rule())
        mgr = TuningRuleManager(rules_dir=tmp_path)
        assert mgr.errors == {}
        assert list(mgr.get_rules()) == ["t1"]

    @pytest.mark.parametrize("mutate", [
        # 缺少必填字段 key/name
        lambda d: d.pop("key"),
        # affix_pool 符号不在词汇表
        lambda d: d["variants"]["default"]["affix_pool"].append("神速"),
        # 槽数不满足 必选+可选+首 == 5
        lambda d: d["variants"]["default"]["patterns"]["环"].update(
            optional_n=3),
        # not_together 须恰好 2 个符号
        lambda d: d["variants"]["default"]["patterns"]["环"].update(
            top=[{"not_together": ["大外", "劲", "敏"]}]),
        # 未知条件原语
        lambda d: d["variants"]["default"]["patterns"]["环"].update(
            top=[{"unknown_kind": ["大外"]}]),
        # 未知部位 key
        lambda d: d["variants"]["default"]["patterns"].update(
            鞋子={"first": ["大外"], "required": [["劲"]], "optional_n": 3}),
        # 变体 key 须为 default 或子流派 key
        lambda d: d["variants"].update(
            extra=d["variants"]["default"]),
        # weapons 引用未定义的子流派
        lambda d: d.update(weapons={"nosub": {"main": {"剑": "剑武学增伤"}}}),
        # own_attr 非法
        lambda d: d.update(own_attr="外功"),
        # optional_pool 超出 affix_pool
        lambda d: d["variants"]["default"].update(optional_pool=["敏"]),
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
        bad = minimal_rule(key="t2")
        bad["variants"]["default"]["affix_pool"] = ["神速"]
        write_rule(tmp_path, bad, name="t2.yaml")
        mgr = TuningRuleManager(rules_dir=tmp_path)
        assert list(mgr.get_rules()) == ["t1"]
        assert "t2" in mgr.errors

    def test_validate_returns_message(self, tmp_path):
        mgr = TuningRuleManager(rules_dir=tmp_path)
        assert mgr.validate(minimal_rule()) is None
        bad = minimal_rule()
        bad["variants"]["default"]["affix_pool"] = ["神速"]
        msg = mgr.validate(bad)
        assert msg and "神速" in msg


# ─── 保存与 get_raw ────────────────────────────────────────

class TestSaveAndRaw:
    def test_save_rule_reloads(self, tmp_path):
        write_rule(tmp_path, minimal_rule())
        mgr = TuningRuleManager(rules_dir=tmp_path)
        data = mgr.get_raw("t1")
        data["name"] = "改名流派"
        mgr.save_rule("t1", data)
        assert mgr.get_rule("t1").name == "改名流派"

    def test_save_invalid_raises_and_keeps_file(self, tmp_path):
        write_rule(tmp_path, minimal_rule())
        mgr = TuningRuleManager(rules_dir=tmp_path)
        bad = mgr.get_raw("t1")
        bad["variants"]["default"]["affix_pool"] = ["神速"]
        with pytest.raises(RuleValidationError):
            mgr.save_rule("t1", bad)
        assert mgr.get_rule("t1").name == "测试流派"  # 原文件未被破坏

    def test_get_raw_is_deepcopy(self, tmp_path):
        write_rule(tmp_path, minimal_rule())
        mgr = TuningRuleManager(rules_dir=tmp_path)
        raw = mgr.get_raw("t1")
        raw["variants"]["default"]["affix_pool"].append("神速")
        assert "神速" not in mgr.get_raw("t1")["variants"]["default"]["affix_pool"]


# ─── 标准字段名守护 ────────────────────────────────────────

def _standard_names() -> set[str]:
    """attributes.yaml affix_caps 各分类 _aliases 的全称词条全集"""
    path = PROJECT_ROOT / "config" / "system" / "yysls" / "attributes.yaml"
    with open(path, "r", encoding="utf-8") as f:
        caps = yaml.safe_load(f)["affix_caps"]
    names: set[str] = set()
    for entry in caps.values():
        names.update(entry.get("_aliases") or [])
    return names


class TestStandardFieldNames:
    def test_rule_affix_names_are_standard(self):
        # 规则内出现的全称词条（非符号、非 DMG 占位符）必须是标准字段名，
        # 否则判定时与 OCR 解析结果对不上
        standard = _standard_names()
        for rule in get_school_rules().values():
            used: set[str] = set()
            for entry in rule.weapons.values():
                used.update(entry.main.values())
            for variant in rule.variants.values():
                for pattern in variant.patterns.values():
                    for slot in pattern.required:
                        used.update(
                            c for c in slot
                            if c not in SYMBOL_VOCAB and c != "DMG")
                    if pattern.required_damage not in (None, "DMG"):
                        used.add(pattern.required_damage)
                    if pattern.damage_pvp_substitute:
                        used.add(pattern.damage_pvp_substitute)
                    used.update(pattern.allowed_divine_pvp)
            unknown = used - standard
            assert not unknown, f"{rule.key} 存在非标准字段名: {unknown}"

    def test_pvp_names_are_standard(self):
        assert PVP_NAMES <= _standard_names()
