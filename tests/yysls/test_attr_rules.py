"""AttrRuleManager（属性规则管理器）测试

此前仅被判定器/解析器间接覆盖，本文件针对
词条别名归一、上限查询（含承音值）与品阶推断补充直测。
数值断言与 config/system/yysls/attributes.yaml 保持一致。
"""

import pytest

from src.apps.yysls.evaluator.attr_rules import (
    POOL_DINGYIN,
    POOL_NORMAL,
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


# ─── 词条分组（_aliases dict 形态）──────────────────────

class TestAliasGroups:
    def test_grouped_aliases_resolved(self, mgr):
        # 指定技能增效 按十大流派分组，组内词条名同样归一到类别
        assert mgr.resolve_affix_category("无名剑法武学技增伤") == "指定技能增效"
        assert mgr.resolve_affix_category("千机索天重击增伤") == "指定技能增效"
        assert mgr.resolve_affix_category("明川药典治疗技增疗") == "指定技能增效"

    def test_grouped_alias_is_dingyin(self, mgr):
        # 分组词条名归一后同样穿透到定音词库
        assert mgr.is_dingyin_affix("积矩九剑流血增伤")

    def test_grouped_alias_caps_lookup(self, mgr):
        caps = mgr.get_affix_caps(110, "嗟夫刀法护盾增效")
        assert caps["cap"] == 9.2
        assert caps["chengyin"] == 9.2  # 定音承音 = cap

    def test_get_alias_groups_structure(self, mgr):
        groups = mgr.get_alias_groups("指定技能增效")
        # 十大流派各一组，每组至少 5 个词条名（牵丝·玉 7 条）
        assert len(groups) == 10
        assert all(len(names) >= 5 for names in groups.values())
        assert len(groups["牵丝·玉"]) == 7
        assert "鸣金·虹" in groups
        assert "无名剑法武学技增伤" in groups["鸣金·虹"]

    def test_ungrouped_category_returns_empty(self, mgr):
        # 不分组类别（list 形态）返回空 dict
        assert mgr.get_alias_groups("外功攻击") == {}
        assert mgr.get_alias_groups("未知类别") == {}

    def test_grouped_category_aliases_flattened(self, mgr):
        # get_aliases_for_category 拍平返回全部组内词条名（9 组 × 5 + 牵丝·玉 7）
        aliases = mgr.get_aliases_for_category("指定技能增效")
        assert len(aliases) == 52


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


# ─── 词库类型（普通 / 定音）──────────────────────────────

class TestAffixPool:
    @pytest.mark.parametrize("name", ["外功增益", "属攻增益", "指定技能增效"])
    def test_dingyin_categories(self, mgr, name):
        assert mgr.get_affix_pool(name) == POOL_DINGYIN
        assert mgr.is_dingyin_affix(name)

    @pytest.mark.parametrize("name", ["会心率", "外功攻击", "未知词条"])
    def test_normal_by_default(self, mgr, name):
        assert mgr.get_affix_pool(name) == POOL_NORMAL
        assert not mgr.is_dingyin_affix(name)

    def test_alias_resolved_to_dingyin(self, mgr):
        # 别名先归一到类别再查词库类型
        assert mgr.is_dingyin_affix("外功穿透")
        assert mgr.is_dingyin_affix("属攻穿透")

    def test_dingyin_chengyin_equals_cap(self, mgr):
        # 承音装备定音属性无限制，承音值 = cap（不乘 0.94）
        caps = mgr.get_affix_caps(110, "外功增益")
        assert caps["cap"] == 16.8
        assert caps["chengyin"] == 16.8

    def test_normal_chengyin_discounted(self, mgr):
        caps = mgr.get_affix_caps(110, "会心率")
        assert caps["chengyin"] == round(14 * 0.94, 2)


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
        # head 单值规则：min=max
        assert mgr.infer_quality("冠胄", 110, 8750) == "purple"
        assert mgr.infer_quality("冠胄", 110, 8751) is None

    def test_follow_parts_reuse_target_rules(self, mgr):
        # 胫甲/腕甲 _follow: head，复用冠胄数值
        assert mgr.infer_quality("胫甲", 110, 8750) == "purple"
        assert mgr.infer_quality("腕甲", 110, 7778) == "blue"

    def test_unknown_type_greedy_fallback(self, mgr):
        # 类型未知时贪婪遍历所有部位（防具气血值不重叠可唯一确定）
        assert mgr.infer_quality(None, 110, 19445) == "gold"   # chest
        assert mgr.infer_quality(None, 110, 7778) == "blue"    # head

    def test_unconfigured_level(self, mgr):
        assert mgr.infer_quality("剑", 42, 200) is None


# ─── 武器类型 / 流派配置（顶层 weapon_types / schools）────

class TestWeaponTypesAndSchools:
    def test_get_weapon_types(self, mgr):
        types = mgr.get_weapon_types()
        assert len(types) == 10
        assert {"陌刀", "唐横刀", "剑", "枪", "扇", "伞"} <= set(types)

    def test_get_schools_structure(self, mgr):
        schools = mgr.get_schools()
        assert len(schools) == 10
        assert "裂石·钧" in schools
        cfg = schools["裂石·钧"]
        assert cfg["attr"] == "裂石"
        assert cfg["main"] == {"weapon": "唐横刀", "martial_art": "斩雪刀法", "affix": "唐横刀武学增伤"}
        assert cfg["sub"] == {"weapon": "陌刀", "martial_art": "十方破阵", "affix": "陌刀武学增伤"}

    def test_school_bindings_valid(self, mgr):
        # 属性合法；主/副武器须在注册表内，词条须属于 指定武学增效 类别
        weapons = set(mgr.get_weapon_types())
        for name, cfg in mgr.get_schools().items():
            assert cfg.get("attr") in ("鸣金", "裂石", "破竹", "牵丝"), \
                f"{name}: 属性 {cfg.get('attr')} 非法"
            for key in ("main", "sub"):
                group = cfg.get(key) or {}
                weapon, affix = group.get("weapon"), group.get("affix")
                assert weapon in weapons, f"{name}: {key} 武器 {weapon} 未注册"
                assert mgr.resolve_affix_category(affix) == "指定武学增效", \
                    f"{name}: 词条 {affix} 不属于指定武学增效"


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
