"""AttrRuleManager（属性规则管理器）测试

此前仅被判定器/解析器间接覆盖，本文件针对
词条别名归一、上限查询（含承音值）与品阶推断补充直测。
数值断言与 config/system/attributes.yaml 保持一致。
"""

import pytest

from src.apps.yysls.evaluator.attr_rules import (
    AttrRange,
    LevelRule,
    get_attr_rule_manager,
)


@pytest.fixture(scope="module")
def mgr():
    return get_attr_rule_manager()


# ─── 词条别名归一 ──────────────────────────────────────────

class TestResolveAffixCategory:
    @pytest.mark.parametrize("alias,category", [
        ("最大外功攻击", "外功攻击"),
        ("最大无相攻击", "属性攻击"),
        ("劲", "五维属性"),
        ("剑武学增伤", "指定武学增效"),
        ("对首领单位增伤", "对单位增效"),
        ("单体类奇术增伤", "奇术类增伤"),
    ])
    def test_alias_mapping(self, mgr, alias, category):
        assert mgr.resolve_affix_category(alias) == category

    def test_unknown_name_passthrough(self, mgr):
        assert mgr.resolve_affix_category("未知词条") == "未知词条"

    def test_get_aliases_for_category(self, mgr):
        aliases = mgr.get_aliases_for_category("外功攻击")
        assert set(aliases) == {"最大外功攻击", "最小外功攻击"}


# ─── 词条上限查询 ──────────────────────────────────────────

class TestGetAffixCaps:
    def test_percent_affix_110(self, mgr):
        caps = mgr.get_affix_caps(110, "会心率")
        assert caps["cap"] == 14
        assert caps["unit"] == "%"
        # 承音值 = cap * 0.94
        assert caps["chengyin"] == round(14 * 0.94, 2)

    def test_alias_resolved_before_lookup(self, mgr):
        # 最大外功攻击 → 外功攻击类别，110 阶 cap 121.4
        caps = mgr.get_affix_caps(110, "最大外功攻击")
        assert caps["cap"] == 121.4

    def test_unknown_affix_returns_none(self, mgr):
        assert mgr.get_affix_caps(110, "未知词条") is None

    def test_unconfigured_level_returns_none(self, mgr):
        assert mgr.get_affix_caps(42, "会心率") is None

    def test_all_categories_present(self, mgr):
        categories = mgr.get_all_affix_categories()
        assert "外功攻击" in categories
        assert "指定武学增效" in categories


# ─── 品阶推断 ──────────────────────────────────────────────

class TestInferQuality:
    @pytest.mark.parametrize("value,expected", [
        (232, "gold"),      # 110 阶武器 gold max
        (100, "gold"),      # gold min（区间重叠时 gold 优先）
        (99, "purple"),     # 低于 gold min 落入 purple
        (80, "blue"),       # blue min
        (79, None),         # 低于全部区间
    ])
    def test_weapon_range_boundaries(self, mgr, value, expected):
        assert mgr.infer_quality("剑", 110, value) == expected

    def test_armor_exact_value(self, mgr):
        # armor_other 单值规则：min=max
        assert mgr.infer_quality("冠胄", 110, 8750) == "purple"
        assert mgr.infer_quality("冠胄", 110, 8751) is None

    def test_unknown_type_greedy_fallback(self, mgr):
        # 类型未知时贪婪遍历所有类别（防具气血值不重叠可唯一确定）
        assert mgr.infer_quality(None, 110, 19445) == "gold"   # chest
        assert mgr.infer_quality(None, 110, 7778) == "blue"    # armor_other

    def test_unconfigured_level(self, mgr):
        assert mgr.infer_quality("剑", 42, 200) is None


# ─── 数据结构单元 ──────────────────────────────────────────

class TestLevelRule:
    def test_attr_range_open_ends(self):
        assert AttrRange("gold", min_val=None, max_val=100).contains(0)
        assert AttrRange("gold", min_val=50, max_val=None).contains(9999)
        assert not AttrRange("gold", min_val=50, max_val=100).contains(49)

    def test_first_matching_range_wins(self):
        rule = LevelRule(ranges=[
            AttrRange("gold", 100, 232),
            AttrRange("purple", 90, 209),
        ])
        assert rule.infer_quality(150) == "gold"   # 重叠区间取先声明者
        assert rule.infer_quality(95) == "purple"
        assert rule.infer_quality(50) is None
