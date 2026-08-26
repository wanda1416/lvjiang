"""GameConfigManager（属性规则管理器）测试

此前仅被判定器/解析器间接覆盖，本文件针对
词条别名归一、上限查询（含承音值）与品阶推断补充直测。
数值断言与 config/system/yysls/attributes.yaml 保持一致。
"""

import pytest

from lvjiang.apps.yysls.config import (
    AFFIX_CATEGORY_NAMES,
    EQUIP_PART_NAMES,
    POOL_DINGYIN,
    POOL_NORMAL,
    AttrRange,
    GameConfigManager,
    LevelRule,
    get_game_config,
)


@pytest.fixture(scope="module")
def mgr():
    return get_game_config()


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

    def test_level_chengyin_capabilities(self, mgr):
        levels = {item.level: item for item in mgr.get_level_configs()}
        assert levels[91].allow_chengyin
        assert not levels[100].allow_retransfer
        assert levels[105].allow_chengyin
        assert levels[105].allow_retransfer

    def test_legacy_level_config_gets_capability_defaults(self, tmp_path):
        path = tmp_path / "game_config.yaml"
        path.write_text(
            "level_configs:\n"
            "- level: 90\n"
            "- level: 91\n"
            "- level: 105\n"
            "- level: 110\n"
            "  allow_retransfer: false\n",
            encoding="utf-8",
        )
        levels = {
            item.level: item
            for item in GameConfigManager(path).get_level_configs()
        }
        assert not levels[90].allow_chengyin
        assert levels[91].allow_chengyin
        assert not levels[91].allow_retransfer
        assert levels[105].allow_retransfer
        assert not levels[110].allow_retransfer

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
        assert len(groups) == 11
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
        assert len(aliases) == 57


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


# ─── 词条部位（顶层 affix_parts）──────────────────────────

class TestAffixParts:
    def test_default_all_parts(self, mgr):
        # 未配置的词条默认可出现在全部七个部位
        assert mgr.get_affix_parts("会心率") == list(EQUIP_PART_NAMES)

    def test_configured_subset(self, tmp_path):
        yaml_text = (
            "base_attrs: {}\n"
            "affix_caps: {}\n"
            "affix_parts:\n"
            "  剑武学增伤: [武器]\n"
            "  全武学增伤: [环, 佩]\n"
            "  非法部位词条: [不存在部位]\n"
            "  非法形态词条: 武器\n"
        )
        path = tmp_path / "attributes.yaml"
        path.write_text(yaml_text, encoding="utf-8")
        mgr = GameConfigManager(path)
        assert mgr.get_affix_parts("剑武学增伤") == ["武器"]
        assert mgr.get_affix_parts("全武学增伤") == ["环", "佩"]
        # 非法部位名被过滤、非 list 形态被忽略 → 回退全部位
        assert mgr.get_affix_parts("非法部位词条") == list(EQUIP_PART_NAMES)
        assert mgr.get_affix_parts("非法形态词条") == list(EQUIP_PART_NAMES)


class TestExternalAffixAliases:
    def test_exact_alias_lookup(self, mgr):
        assert mgr.get_affix_names_for_alias("拳甲增") == ["手甲武学增伤"]
        assert mgr.get_affix_names_for_alias("首领增") == ["对首领单位增伤"]

    def test_no_fuzzy_fallback(self, mgr):
        assert mgr.get_affix_names_for_alias("蓄力技") == []
        assert mgr.get_affix_names_for_alias("不存在的增") == []

    def test_reverse_lookup(self, mgr):
        assert mgr.get_affix_aliases("剑武学增伤") == ["剑增"]


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
        assert mgr.is_dingyin_affix("无相穿透")

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
        ([100, 232], "gold"),     # 110 阶武器 gold 区间精确匹配
        ([90, 209], "purple"),    # purple 区间精确匹配
        ([80, 186], "blue"),      # blue 区间精确匹配
        ([100, 233], None),       # 上端不相等 → 不命中
        ([99, 232], None),        # 下端不相等 → 不命中
        (232, None),              # 标量不能命中区间属性
    ])
    def test_weapon_range_exact_match(self, mgr, value, expected):
        # 区间 [a,b] 含义：装备提供 +a 最小、+b 最大外功攻击，
        # 解析出的区间必须两端都相等才算同一品阶，而非“落在区间内”
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


# ─── 仅凭数值反查等级+品阶（equip_level OCR 缺失兵底）────

class TestInferLevelQuality:
    def test_recover_level_and_quality_by_type(self, mgr):
        # 冠胄 110 阶 purple = 8750；type 已知，无需等级即可反查
        assert mgr.infer_level_quality("冠胄", 8750) == (110, "purple")

    def test_recover_by_greedy_when_type_unknown(self, mgr):
        # 类型未知，仅凭数值全局反查（chest 110 gold=19445 / head 110 blue=7778）
        assert mgr.infer_level_quality(None, 19445) == (110, "gold")
        assert mgr.infer_level_quality(None, 7778) == (110, "blue")

    def test_no_match_returns_none_pair(self, mgr):
        assert mgr.infer_level_quality("冠胄", 1) == (None, None)


# ─── 仅凭数值反查部位（equip_type OCR 缺失回填）─────────

class TestInferTypeByValue:
    def test_unique_hit_chest(self, mgr):
        # chest 数值独立 → 唯一命中可回填（chest 110 gold=19445）
        assert mgr.infer_type_by_value(19445) == "胸甲"

    def test_unique_hit_jewelry(self, mgr):
        assert mgr.infer_type_by_value(133) == "环"    # ring 110 gold
        assert mgr.infer_type_by_value(199) == "佩"    # pendant 110 gold

    def test_ambiguous_head_leg_wrist(self, mgr):
        # 冠胄/胫甲/腕甲 _follow 同值，命中 3 个部位 → 无法区分
        assert mgr.infer_type_by_value(7778) is None

    def test_weapon_range_not_participating(self, mgr):
        # 武器 key 对应多种武器类型，不参与反查
        assert mgr.infer_type_by_value([100, 232]) is None

    def test_no_match(self, mgr):
        assert mgr.infer_type_by_value(1) is None


# ─── 武器类型 / 流派配置（顶层 weapon_types / schools）────

class TestWeaponTypesAndSchools:
    def test_get_weapon_types(self, mgr):
        types = mgr.get_weapon_types()
        assert len(types) == 10
        assert {"陌刀", "横刀", "剑", "枪", "扇", "伞"} <= set(types)

    def test_get_weapon_wuxue_affix(self, mgr):
        assert mgr.get_weapon_wuxue_affix("剑") == "剑武学增伤"
        assert mgr.get_weapon_wuxue_affix("扇") == "扇武学增效"
        assert mgr.get_weapon_wuxue_affix("未注册") == ""

    def test_get_schools_structure(self, mgr):
        schools = mgr.get_schools()
        assert len(schools) == 11
        assert "裂石·钧" in schools
        cfg = schools["裂石·钧"]
        assert cfg["attr"] == "裂石"
        assert cfg["main"] == {"weapon": "横刀", "martial_art": "斩雪刀法"}
        assert cfg["sub"] == {"weapon": "陌刀", "martial_art": "十方破阵"}
        # 新增流派
        assert "破竹·樽" in schools
        cfg_zun = schools["破竹·樽"]
        assert cfg_zun["attr"] == "破竹"
        assert cfg_zun["main"] == {"weapon": "手甲", "martial_art": "悬身拳法"}
        assert cfg_zun["sub"] == {"weapon": "双刀", "martial_art": "断水双诀"}

    def test_graduation_schemes(self, mgr):
        # 已注册流派至少包含基础方案
        schemes = mgr.get_graduation_schemes("鸣金·虹")
        assert "基础方案" in schemes
        # 未注册流派返回空
        assert mgr.get_graduation_schemes("未注册流派") == []

    def test_school_bindings_valid(self, mgr):
        # 属性合法；主/副武器须在注册表内
        weapons = set(mgr.get_weapon_types())
        for name, cfg in mgr.get_schools().items():
            assert cfg.get("attr") in ("鸣金", "裂石", "破竹", "牵丝"), \
                f"{name}: 属性 {cfg.get('attr')} 非法"
            for key in ("main", "sub"):
                group = cfg.get(key) or {}
                weapon = group.get("weapon")
                assert weapon in weapons, f"{name}: {key} 武器 {weapon} 未注册"


# ─── 指定武学增效词条数据源 ──────────────────────────

class TestWuxueAffixNames:
    def test_names_come_from_category(self, mgr):
        # 调律规则 UI 增伤词条候选的唯一来源
        names = mgr.get_wuxue_affix_names()
        assert names, "指定武学增效词条不应为空"
        assert "剑武学增伤" in names
        assert all(mgr.resolve_affix_category(n) == "指定武学增效"
                   for n in names)

    def test_weapon_bound_affixes_within_names(self, mgr):
        # 游戏配置中每个武器绑定的武学增效须在词条全集内
        names = set(mgr.get_wuxue_affix_names())
        for weapon, affix in mgr.get_all_weapon_wuxue_affixes().items():
            assert affix in names, f"{weapon}: 绑定词条 {affix} 不在全集内"


# ─── 数据结构单元 ──────────────────────────────────────────

class TestAffixCategories:
    def test_categories_ordered_six(self, mgr):
        # 固定 6 类归属，顺序与 AFFIX_CATEGORY_NAMES 一致
        categories = mgr.get_affix_categories()
        assert list(categories.keys()) == list(AFFIX_CATEGORY_NAMES)

    def test_returns_copy_not_reference(self, mgr):
        # 返回副本，外部修改不影响内部状态
        first = mgr.get_affix_categories()
        first["外功类"].append("污染项")
        assert "污染项" not in mgr.get_affix_categories()["外功类"]

    @pytest.mark.parametrize("affix,category", [
        ("最大外功攻击", "外功类"),
        ("劲", "外功类"),
        ("势", "外功类"),
        ("最大无相攻击", "属攻类"),
        ("最大牵丝攻击", "属攻类"),
        ("会意率", "三率类"),
        ("全武学增效", "增效类"),
        ("单体类奇术增伤", "增效类"),
        ("剑武学增伤", "武器类"),
        ("扇武学增效", "武器类"),
        ("气血最大值", "生存类"),
    ])
    def test_affix_to_category_mapping(self, mgr, affix, category):
        assert mgr.get_affix_category(affix) == category

    def test_reverse_matches_forward(self, mgr):
        # 正反映射一致：每类下的词条反查均归回该类
        for cat, names in mgr.get_affix_categories().items():
            for name in names:
                assert mgr.get_affix_category(name) == cat

    def test_unknown_affix_returns_empty(self, mgr):
        assert mgr.get_affix_category("未归类词条") == ""


class TestLevelRule:
    def test_range_attr_exact_endpoints(self):
        # 区间属性：两端都相等才命中
        r = AttrRange("gold", min_val=100, max_val=232)
        assert r.matches([100, 232])
        assert not r.matches([100, 231])   # 上端不等
        assert not r.matches([99, 232])    # 下端不等
        assert not r.matches(232)          # 标量不命中区间

    def test_point_attr_exact_value(self):
        # 点值属性（min==max）：标量精确相等才命中
        r = AttrRange("purple", min_val=8750, max_val=8750)
        assert r.matches(8750)
        assert not r.matches(8751)
        assert r.matches([8750, 8750])     # 同值区间形式也命中

    def test_first_matching_range_wins(self):
        # 精确匹配下相邻品阶区间不再互相遮蔽
        rule = LevelRule(ranges=[
            AttrRange("gold", 100, 232),
            AttrRange("purple", 90, 209),
        ])
        assert rule.infer_quality([100, 232]) == "gold"
        assert rule.infer_quality([90, 209]) == "purple"
        assert rule.infer_quality([100, 209]) is None   # 端点不成对
        assert rule.infer_quality(150) is None           # 标量不匹配区间
