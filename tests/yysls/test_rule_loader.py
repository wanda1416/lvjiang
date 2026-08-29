"""调律规则加载器测试

覆盖 TuningRuleManager 的加载/排序/校验拒绝/保存与 get_raw 深拷贝、
create_rule/delete_rule、playstyles 节（含 attr）、4 条件原语与
条件组三种形态（单键 dict / list=AND / when+all 开关组）解析、
default_rating、tune_config 开关注册表（switches），以及规则内
词条名与规则可引用词表（rule_affix_candidates：标准词条全集
+ 四个动态词条）的一致性守护。
"""

from pathlib import Path

import pytest
import yaml

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.core.evaluator import get_tuning_rules
from lvjiang.apps.yysls.core.tuning_rules import (
    DYNAMIC_AFFIXES,
    MAX_TUNE_RESETS,
    QUALITY_PARTS,
    BehaviorRule,
    FoodRule,
    MaterialSettings,
    RuleValidationError,
    ScanBehavior,
    TuneBehavior,
    TuningGroupManager,
    TuningRuleManager,
    dynamic_affix_map,
    get_tune_config,
    get_tuning_group,
    get_tuning_rule_manager,
    parse_tune_config,
    parse_tuning_group,
    parse_tuning_rule,
    rule_affix_candidates,
    specific_attr_names,
    standard_affix_names,
    standard_playstyle_attrs,
)
from lvjiang.core.config import versioning
from lvjiang.core.config.resolver import ConfigResolver, SystemContentProtected


def minimal_rule(**overrides) -> dict:
    """构造一份最小合法规则 dict（测试按需覆盖字段制造非法样本）"""
    data = {
        "key": "t1",
        "name": "测试规则",
        "playstyles": {
            "测试": {
                "main": {"weapon": "剑", "damage": "剑武学增伤"},
                "sub": {"weapon": "枪", "damage": None},
                "attr": "通用",
            },
        },
        "affix_pool": ["最大外功攻击", "劲"],
        "patterns": {
            "环": {
                "first": ["最大外功攻击"],
                "junk_conditions": [
                    {"count_max": {"symbols": ["劲"], "max": 0}}],
                "top_conditions": [
                    [{"contains_all": ["劲"]},
                     {"count_max": {"symbols": ["最大外功攻击"],
                                    "max": 0}}],
                ],
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


def _valid_group() -> dict:
    """构造一份最小合法基础规则组 dict（测试按需覆盖字段制造非法样本）"""
    return {
        "key": "t1",
        "name": "测试规则组",
        "min_level": 100,
        "materials": {},
        "scan": {},
        "tune": {},
    }


# ─── 内置规则加载 ──────────────────────────────────────────

class TestBuiltinRules:
    def test_all_loaded_without_errors(self):
        mgr = get_tuning_rule_manager()
        assert mgr.errors == {}
        # 规则数量随文件增加，不硬编码列表
        assert len(list(mgr.get_rules())) >= 5

    def test_order_matches_tune_config(self):
        """规则顺序与 tune_config.yaml tuning_rules 声明顺序一致"""
        rules = get_tuning_rules()
        from lvjiang.apps.yysls.core.tuning_rules import get_tune_config
        tuning_rules = get_tune_config().tuning_rules
        declared = [k for k in tuning_rules if k in rules]
        undeclared = sorted(k for k in rules if k not in tuning_rules)
        assert list(rules.keys()) == declared + undeclared

    def test_required_fields_present(self):
        for rule in get_tuning_rules().values():
            assert rule.key and rule.name
            if rule.patterns:  # 骨架规则无 pattern，跳过
                assert rule.affix_pool
                for pattern in rule.patterns.values():
                    assert pattern.first

    def test_jewelry_requires_all_martial_bonus_except_heal_fire(self):
        """环与佩共用「环」规则；只有治疗火拳允许没有全武学增效。"""
        for key, rule in get_tuning_rules().items():
            pattern = rule.patterns["环"]
            requires_bonus = any(
                len(group.conditions) == 1
                and group.conditions[0].kind == "count_max"
                and group.conditions[0].symbols == ["全武学增效"]
                and group.conditions[0].max == 0
                for group in pattern.junk_conditions
            )
            assert requires_bonus is (key != "heal_fire"), (key, rule.name)

    def test_pattern_affixes_are_all_in_the_rule_own_pool(self):
        """pattern 引用的词条必须在本规则 affix_pool 内。

        判定第一步就把池外词条判成垃圾，轮不到四档条件。所以一旦某个
        条件（尤其是带 when 开关的那种）引用了池外词条，这个开关就是
        死的：用户打开「保留 XX」，装备照样被回收，且回收不可逆。
        解析层只按全局词表查名，不管是不是本规则池内的，拦不住这种。
        """
        for key, rule in get_tuning_rules().items():
            pool = set(rule.affix_pool or [])
            if not pool:          # 骨架规则无池，跳过
                continue
            for part, pattern in (rule.patterns or {}).items():
                used = set(pattern.first or [])
                for tier in ("junk_conditions", "normal_conditions",
                             "excellent_conditions", "top_conditions"):
                    for group in getattr(pattern, tier, []) or []:
                        for cond in group.conditions:
                            used |= set(cond.symbols or [])
                missing = sorted(a for a in used if a and a not in pool)
                assert not missing, (
                    f"{key} 的 {part} 引用了池外词条 {missing}，"
                    f"这些条件永远不会生效")

    def test_playstyles_per_plan(self):
        # 玩法定义持续变更：不硬编码内容，对照 YAML 原文校验解析
        mgr = get_tuning_rule_manager()
        for key, rule in get_tuning_rules().items():
            raw = mgr.get_raw(key).get("playstyles") or {}
            assert raw, key  # 每个规则至少一条玩法
            assert set(rule.playstyles) == set(raw)
            for name, ps in rule.playstyles.items():
                assert ps.main.weapon == (raw[name]["main"].get("weapon") or "")
                assert ps.main.damage == raw[name]["main"].get("damage")
                # 摘要供 UI 勾选项展示
                assert rule.playstyle_options[name] == (
                    f"主 {ps.main.weapon} / 副 {ps.sub.weapon}")
        # 火拳主扇不需要增伤
        fire = get_tuning_rules()["heal_fire"].playstyles["火拳"]
        assert fire.main.weapon == "扇" and fire.main.damage is None

    def test_playstyle_weapons_match_school_registry(self):
        """所有内置玩法的主副武器必须与全局流派注册一致。"""
        # value = (正式流派名, 是否有意对调主副手)。裂石·钧的“双切”
        # 是唯一反向持有武器的玩法，其余玩法均直接沿用注册表顺序。
        bindings = {
            ("huiyi_general", "无名"): ("鸣金·虹", False),
            ("huiyi_general", "火九"): ("鸣金·影", False),
            ("huixin_small", "纯唐"): ("裂石·钧", False),
            ("huixin_small", "双切"): ("裂石·钧", True),
            ("huixin_small", "鸢鸢"): ("破竹·鸢", False),
            ("huixin_small", "双刀"): ("破竹·风", False),
            ("huixin_small", "尘尘"): ("破竹·尘", False),
            ("huixin_small", "翊翊"): ("牵丝·翊", False),
            ("huixin_small", "樽樽"): ("破竹·樽", False),
            ("huixin_big", "纯唐"): ("裂石·钧", False),
            ("huixin_big", "双切"): ("裂石·钧", True),
            ("huixin_big", "威威"): ("裂石·威", False),
            ("huixin_big", "鸢鸢"): ("破竹·鸢", False),
            ("huixin_big", "双刀"): ("破竹·风", False),
            ("huixin_big", "尘尘"): ("破竹·尘", False),
            ("huixin_big", "翊翊"): ("牵丝·翊", False),
            ("huixin_big", "樽樽"): ("破竹·樽", False),
            ("huixin_modao", "威威"): ("裂石·威", False),
            ("huixin_yuyu", "走地玉"): ("牵丝·玉", False),
            ("huixin_yuyu", "飞天玉"): ("牵丝·玉", False),
            ("heal_pure", "纯奶"): ("牵丝·霖", False),
            ("heal_fire", "火拳"): ("牵丝·霖", False),
        }
        rules = get_tuning_rules()
        actual = {
            (rule_key, playstyle)
            for rule_key, rule in rules.items()
            for playstyle in rule.playstyles
        }
        assert actual == set(bindings), "新增玩法时必须登记对应正式流派"

        game = get_game_config()
        schools = game.get_schools()
        for (rule_key, playstyle), (school, reversed_sides) in bindings.items():
            cfg = schools[school]
            expected = (
                cfg["main"]["weapon"], cfg["sub"]["weapon"])
            if reversed_sides:
                expected = expected[::-1]
            plan = rules[rule_key].playstyles[playstyle]
            assert (plan.main.weapon, plan.sub.weapon) == expected, (
                rule_key, playstyle, school)

            # 声明需要武学增伤时，词条必须属于同侧武器；允许显式为 None。
            for side in (plan.main, plan.sub):
                if side.damage is not None:
                    assert side.damage == game.get_weapon_wuxue_affix(
                        side.weapon), (rule_key, playstyle, side.weapon)

    def test_playstyle_attr_per_plan(self):
        # attr 随配置变更：只校验解析值与 YAML 一致且在合法集内
        mgr = get_tuning_rule_manager()
        vocab = set(standard_playstyle_attrs())
        for key, rule in get_tuning_rules().items():
            raw = mgr.get_raw(key).get("playstyles") or {}
            for name, ps in rule.playstyles.items():
                assert ps.attr in vocab
                expected = raw[name].get("attr") or "通用"
                assert ps.attr == expected, (key, name)

    def test_when_references_registered_switches(self):
        # 内置规则条件组 when 引用的开关全部已在注册表登记
        registered = set(get_tune_config().switches)
        for key, rule in get_tuning_rules().items():
            assert rule.referenced_switches() <= registered, key


# ─── schema 校验拒绝 ───────────────────────────────────────

class TestValidation:
    def test_minimal_rule_valid(self, tmp_path):
        write_rule(tmp_path, minimal_rule())
        mgr = TuningRuleManager(rules_dir=tmp_path)
        assert mgr.errors == {}
        assert list(mgr.get_rules()) == ["t1"]

    def test_empty_skeleton_valid(self, tmp_path):
        """空 playstyles/affix_pool/patterns 的骨架规则可保存（新建规则）"""
        write_rule(tmp_path, minimal_rule(
            playstyles={}, affix_pool=[], patterns={}))
        mgr = TuningRuleManager(rules_dir=tmp_path)
        assert mgr.errors == {}
        rule = mgr.get_rule("t1")
        assert rule.playstyles == {}
        assert rule.affix_pool == [] and rule.patterns == {}

    def test_condition_group_syntax(self, tmp_path):
        """单键 dict = 单条件组；嵌套 list = 组内 AND"""
        write_rule(tmp_path, minimal_rule())
        mgr = TuningRuleManager(rules_dir=tmp_path)
        pattern = mgr.get_rule("t1").patterns["环"]
        assert len(pattern.junk_conditions) == 1
        assert len(pattern.junk_conditions[0].conditions) == 1  # 单条件组
        assert pattern.junk_conditions[0].when == {}  # 无前提 = 恒生效
        assert len(pattern.top_conditions) == 1
        assert len(pattern.top_conditions[0].conditions) == 2   # 组内 AND
        assert pattern.normal_conditions == []
        assert pattern.excellent_conditions == []

    def test_when_group_syntax(self):
        """{when: {...}, all: [...]} = 带开关前提的条件组"""
        data = minimal_rule()
        data["patterns"]["环"]["junk_conditions"] = [
            {"when": {"keep_pvp": False},
             "all": [{"contains_all": ["劲"]}]},
        ]
        rule = parse_tuning_rule(data, switch_keys={"keep_pvp"})
        group = rule.patterns["环"].junk_conditions[0]
        assert group.when == {"keep_pvp": False}
        assert len(group.conditions) == 1
        assert group.conditions[0].kind == "contains_all"
        # active：when 全匹配才参与，未配置的开关视作 False
        assert group.active({}) is True
        assert group.active({"keep_pvp": False}) is True
        assert group.active({"keep_pvp": True}) is False

    def test_when_unknown_switch(self):
        """when 引用未注册开关：传 switch_keys 时报错，None 跳过校验"""
        data = minimal_rule()
        data["patterns"]["环"]["junk_conditions"] = [
            {"when": {"nonexistent": True},
             "all": [{"contains_all": ["劲"]}]},
        ]
        parse_tuning_rule(data)  # 离线解析不校验
        with pytest.raises(RuleValidationError, match="未注册"):
            parse_tuning_rule(data, switch_keys={"keep_pvp"})

    def test_playstyle_switch_parsed(self):
        """玩法绑定开关 switch 字段解析：缺省 None，有值时记录"""
        data = minimal_rule()
        # 缺省无 switch
        rule = parse_tuning_rule(data)
        assert rule.playstyles["测试"].switch is None
        # 有 switch
        data["playstyles"]["测试"]["switch"] = "keep_pvp"
        rule = parse_tuning_rule(data, switch_keys={"keep_pvp"})
        assert rule.playstyles["测试"].switch == "keep_pvp"

    def test_playstyle_switch_in_referenced_switches(self):
        """玩法绑定开关计入 referenced_switches"""
        data = minimal_rule()
        data["playstyles"]["测试"]["switch"] = "keep_pvp"
        rule = parse_tuning_rule(data, switch_keys={"keep_pvp"})
        assert "keep_pvp" in rule.referenced_switches()

    def test_playstyle_switch_rejected(self):
        """玩法绑定开关：未注册 key 或非法格式均拒绝"""
        data = minimal_rule()
        # 未注册 key
        data["playstyles"]["测试"]["switch"] = "nonexistent"
        with pytest.raises(RuleValidationError, match="未注册"):
            parse_tuning_rule(data, switch_keys={"keep_pvp"})
        # 非法格式
        data["playstyles"]["测试"]["switch"] = "Bad-Key"
        with pytest.raises(RuleValidationError, match="非法"):
            parse_tuning_rule(data)

    def test_include_first_all_kinds(self):
        """include_first 全原语可用：集合式原语的 dict 形态"""
        data = minimal_rule()
        data["patterns"]["环"]["excellent_conditions"] = [
            [{"contains_all": {"symbols": ["劲"], "include_first": True}},
             {"count_min": {"symbols": ["劲"], "min": 1,
                            "include_first": True}}],
        ]
        rule = parse_tuning_rule(data)
        conds = rule.patterns["环"].excellent_conditions[0].conditions
        assert conds[0].kind == "contains_all"
        assert conds[0].include_first is True
        assert conds[1].kind == "count_min" and conds[1].min == 1
        assert conds[1].include_first is True

    def test_not_together_three_symbols_valid(self):
        """not_together 放开为 ≥2 词条"""
        data = minimal_rule()
        data["patterns"]["环"]["top_conditions"] = [
            {"not_together": ["最大外功攻击", "劲", "剑武学增伤"]}]
        rule = parse_tuning_rule(data)
        cond = rule.patterns["环"].top_conditions[0].conditions[0]
        assert cond.kind == "not_together" and len(cond.symbols) == 3

    def test_default_rating_parsed(self):
        assert parse_tuning_rule(minimal_rule()).default_rating == "excellent"
        rule = parse_tuning_rule(minimal_rule(default_rating="junk"))
        assert rule.default_rating == "junk"

    def test_pattern_default_rating_parsed(self):
        """部位级默认判定：缺省 None（跟随规则级），可覆盖为四档之一"""
        assert parse_tuning_rule(
            minimal_rule()).patterns["环"].default_rating is None
        data = minimal_rule()
        data["patterns"]["环"]["default_rating"] = "top"
        assert parse_tuning_rule(data).patterns["环"].default_rating == "top"

    def test_common_conditions_parsed(self):
        """通用判定：规则级四档条件解析，when 引用计入开关校验"""
        data = minimal_rule(common_conditions={
            "junk_conditions": [{"contains_all": ["劲"]}],
            "top_conditions": [
                {"when": {"keep_pvp": True},
                 "all": [{"contains_all": ["劲"]}]},
            ],
        })
        rule = parse_tuning_rule(data, switch_keys={"keep_pvp"})
        assert len(rule.common.junk_conditions) == 1
        assert rule.common.normal_conditions == []
        assert rule.common.excellent_conditions == []
        assert rule.common.top_conditions[0].when == {"keep_pvp": True}
        assert "keep_pvp" in rule.referenced_switches()
        # when 引用未注册开关同样拒绝
        with pytest.raises(RuleValidationError, match="未注册"):
            parse_tuning_rule(data, switch_keys=set())

    def test_common_conditions_default_empty(self):
        """缺省无通用判定：四档均为空"""
        rule = parse_tuning_rule(minimal_rule())
        assert rule.common.junk_conditions == []
        assert rule.common.normal_conditions == []
        assert rule.common.excellent_conditions == []
        assert rule.common.top_conditions == []

    def test_common_conditions_rejects_bad_shape(self):
        """通用判定只允许四档条件键（无 first/default_rating）"""
        with pytest.raises(RuleValidationError, match="common_conditions"):
            parse_tuning_rule(minimal_rule(
                common_conditions={"first": ["劲"]}))
        with pytest.raises(RuleValidationError, match="common_conditions"):
            parse_tuning_rule(minimal_rule(
                common_conditions={"default_rating": "top"}))
        with pytest.raises(RuleValidationError, match="common_conditions"):
            parse_tuning_rule(minimal_rule(
                common_conditions=[{"contains_all": ["劲"]}]))

    @pytest.mark.parametrize("mutate", [
        # 缺少必填字段 key/name
        lambda d: d.pop("key"),
        # 旧版 schema 字段不再支持
        lambda d: d.update(variants={"default": {}}),
        lambda d: d.update(sub_schools={"lieshi": {"name": "裂石"}}),
        lambda d: d.update(optional_pool=["劲"]),
        lambda d: d.update(junk_rules=[]),
        lambda d: d.update(has_keep_pvp=True),
        # default_rating 非四档枚举
        lambda d: d.update(default_rating="great"),
        # 部位级 default_rating 非四档枚举
        lambda d: d["patterns"]["环"].update(default_rating="great"),
        # affix_pool 词条不在标准词条全集
        lambda d: d["affix_pool"].append("大外"),
        # first 不能为空
        lambda d: d["patterns"]["环"].update(first=[]),
        # not_contains 已废弃（改用 count_max max=0）
        lambda d: d["patterns"]["环"].update(
            junk_conditions=[{"not_contains": ["劲"]}]),
        # usable_conditions 已废弃（改用 normal_conditions）
        lambda d: d["patterns"]["环"].update(
            usable_conditions=[{"contains_all": ["劲"]}]),
        # not_together 须至少 2 个词条
        lambda d: d["patterns"]["环"].update(
            top_conditions=[{"not_together": ["劲"]}]),
        # 未知条件原语
        lambda d: d["patterns"]["环"].update(
            top_conditions=[{"unknown_kind": ["最大外功攻击"]}]),
        # 计数原语参数必须是 dict
        lambda d: d["patterns"]["环"].update(
            top_conditions=[{"count_max": ["劲"]}]),
        # 条件词条列表不能为空
        lambda d: d["patterns"]["环"].update(
            top_conditions=[{"contains_all": []}]),
        # 条件组必须是 dict 或 dict 列表
        lambda d: d["patterns"]["环"].update(junk_conditions=["oops"]),
        # when 开关 key 格式非法
        lambda d: d["patterns"]["环"].update(junk_conditions=[
            {"when": {"Bad-Key": True},
             "all": [{"contains_all": ["劲"]}]}]),
        # when 期望值必须是 bool
        lambda d: d["patterns"]["环"].update(junk_conditions=[
            {"when": {"keep_pvp": "yes"},
             "all": [{"contains_all": ["劲"]}]}]),
        # 开关条件组只允许 when/all 键
        lambda d: d["patterns"]["环"].update(junk_conditions=[
            {"when": {"keep_pvp": True},
             "all": [{"contains_all": ["劲"]}], "extra": 1}]),
        # 未知部位 key
        lambda d: d["patterns"].update(
            鞋子={"first": ["最大外功攻击"]}),
        # playstyles 武器名不能为空
        lambda d: d["playstyles"]["测试"]["main"].update(weapon=""),
        # playstyles 增伤词条不在标准词条全集
        lambda d: d["playstyles"]["测试"]["main"].update(damage="神速"),
        # playstyles.attr 不在属性攻击词组内
        lambda d: d["playstyles"]["测试"].update(attr="不存在属性"),
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

    def test_specific_attr_allowed(self):
        # 真实属攻词条合法：单一流派/混搭规则均可字面引用
        rule = parse_tuning_rule(minimal_rule(
            affix_pool=["最大外功攻击", "最大裂石攻击"]))
        assert "最大裂石攻击" in rule.pool_set
        data = minimal_rule()
        data["affix_pool"].append("最小鸣金攻击")
        data["patterns"]["环"]["junk_conditions"] = [
            {"count_max": {"symbols": ["最小鸣金攻击"], "max": 0}}]
        parse_tuning_rule(data)  # 不应抛出

    def test_dynamic_affix_allowed_for_specific_attr(self):
        # 具体属性玩法可引用动态词条
        data = minimal_rule(affix_pool=[
            "最大外功攻击", "最大本属攻击", "最小外属攻击"])
        data["playstyles"]["测试"]["attr"] = "裂石"
        rule = parse_tuning_rule(data)
        assert "最大本属攻击" in rule.pool_set

    def test_dynamic_affix_rejected_with_generic_playstyle(self):
        # 含 attr=通用 玩法（混搭流）的规则禁用动态词条
        # （minimal_rule 的玩法即通用）
        with pytest.raises(RuleValidationError, match="混搭流"):
            parse_tuning_rule(minimal_rule(
                affix_pool=["最大外功攻击", "最大本属攻击"]))
        # 四档条件中引用同样拒绝
        data = minimal_rule()
        data["patterns"]["环"]["junk_conditions"] = [
            {"count_min": {"symbols": ["最小本属攻击", "最小外属攻击"],
                           "min": 2}}]
        with pytest.raises(RuleValidationError, match="动态属攻"):
            parse_tuning_rule(data)
        # 混合玩法（通用+具体）同样拒绝（严格口径）
        data = minimal_rule(affix_pool=["最大外功攻击", "最大本属攻击"])
        data["playstyles"]["另一个"] = {
            "main": {"weapon": "剑", "damage": None},
            "sub": {"weapon": "枪", "damage": None},
            "attr": "裂石",
        }
        with pytest.raises(RuleValidationError, match="混搭流"):
            parse_tuning_rule(data)


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

    def test_rule_version_bump_is_explicit_and_immediate(
            self, tmp_path, monkeypatch):
        monkeypatch.setitem(
            versioning.VERSIONED_DIRS,
            "yysls/tuning_rules",
            versioning.VersionedDir(
                "yysls/tuning_rules", "*.yaml", 1, allow_remote_new=True),
        )
        system = tmp_path / "system"
        local = tmp_path / "local"
        remote = tmp_path / "remote"
        rules = system / "yysls" / "tuning_rules"
        rules.mkdir(parents=True)
        data = {"content_version": 1, **minimal_rule()}
        (rules / "t1.yaml").write_text(
            yaml.dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        remote_rules = remote / "yysls" / "tuning_rules"
        remote_rules.mkdir(parents=True)
        remote_data = {"content_version": 3, **minimal_rule(name="远程规则")}
        (remote_rules / "t1.yaml").write_text(
            yaml.dump(remote_data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

        scratch = tmp_path / "scratch"
        scratch.mkdir()
        mgr = TuningRuleManager(rules_dir=scratch)
        mgr._resolver = ConfigResolver(  # type: ignore[attr-defined]
            system_dir=system, local_dir=local, remote_dir=remote,
            dev_mode=True)
        mgr._rel_dir = "yysls/tuning_rules"  # type: ignore[attr-defined]
        mgr.reload()

        edited = mgr.get_raw("t1")
        edited["name"] = "普通保存"
        mgr.save_rule("t1", edited)
        assert versioning.read_version(rules / "t1.yaml") == 1
        assert mgr.get_rule("t1").name == "远程规则"

        # 普通保存写进 system 了，但线上版本更高、reload 读回的仍是线上那份，
        # 规则实际没生效。UI 必须能问出这个状态，否则只会报「已保存并生效」，
        # 作者对着没变化的界面白排查。
        override = mgr.system_save_override("t1")
        assert override is not None
        assert (override.layer, override.version) == ("remote", 3)

        assert mgr.bump_rule_version("t1", edited) == 4
        assert versioning.read_version(rules / "t1.yaml") == 4
        assert mgr.get_rule("t1").name == "普通保存"
        assert mgr.system_save_override("t1") is None      # 提升后真生效了

    def test_not_superseded_without_remote(self, tmp_path):
        mgr = TuningRuleManager(rules_dir=tmp_path)
        mgr.create_rule("plain", "普通规则")
        assert mgr.system_save_override("plain") is None

    def test_dev_system_save_reports_local_override(self, tmp_path):
        system, local, remote = (tmp_path / n for n in ("system", "local", "remote"))
        for root, name in ((system, "系统规则"), (local, "本地规则")):
            directory = root / "yysls" / "tuning_rules"
            directory.mkdir(parents=True)
            (directory / "t1.yaml").write_text(
                yaml.dump({"content_version": 1, **minimal_rule(name=name)},
                          allow_unicode=True, sort_keys=False),
                encoding="utf-8")
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        mgr = TuningRuleManager(rules_dir=scratch)
        mgr._resolver = ConfigResolver(  # type: ignore[attr-defined]
            system_dir=system, local_dir=local, remote_dir=remote,
            dev_mode=True)
        mgr._rel_dir = "yysls/tuning_rules"  # type: ignore[attr-defined]
        mgr.reload()

        override = mgr.system_save_override("t1")
        assert override is not None
        assert (override.layer, override.version) == ("local", 1)

    def test_user_mode_never_reports_superseded(self, tmp_path, monkeypatch):
        """用户模式写的是 local 影子，恒为最高优先级，不存在被顶替的问题"""
        monkeypatch.setitem(
            versioning.VERSIONED_DIRS,
            "yysls/tuning_rules",
            versioning.VersionedDir(
                "yysls/tuning_rules", "*.yaml", 1, allow_remote_new=True),
        )
        system, local, remote = (tmp_path / n for n in ("system", "local", "remote"))
        for root, ver, name in ((system, 1, "系统规则"), (remote, 3, "远程规则")):
            d = root / "yysls" / "tuning_rules"
            d.mkdir(parents=True)
            (d / "t1.yaml").write_text(
                yaml.dump({"content_version": ver, **minimal_rule(name=name)},
                          allow_unicode=True, sort_keys=False),
                encoding="utf-8")
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        mgr = TuningRuleManager(rules_dir=scratch)
        mgr._resolver = ConfigResolver(  # type: ignore[attr-defined]
            system_dir=system, local_dir=local, remote_dir=remote,
            dev_mode=False)
        mgr._rel_dir = "yysls/tuning_rules"  # type: ignore[attr-defined]
        mgr.reload()
        assert mgr.system_save_override("t1") is None


# ─── 创建与删除 ────────────────────────────────────────────

class TestCreateAndDelete:
    def test_rule_names_are_cached_from_reload(self, tmp_path, monkeypatch):
        write_rule(tmp_path, minimal_rule())
        mgr = TuningRuleManager(rules_dir=tmp_path)

        def unexpected_load(*_args, **_kwargs):
            raise AssertionError("query should not parse YAML again")

        monkeypatch.setattr(yaml, "safe_load", unexpected_load)
        assert mgr.get_all_rule_keys_and_names() == [("t1", "测试规则")]

    def test_reload_observes_external_rule_change(self, tmp_path):
        path = write_rule(tmp_path, minimal_rule(name="旧名称"))
        mgr = TuningRuleManager(rules_dir=tmp_path)
        assert mgr.get_all_rule_keys_and_names() == [("t1", "旧名称")]

        path.write_text(
            yaml.dump(minimal_rule(name="新名称"), allow_unicode=True),
            encoding="utf-8",
        )
        mgr.reload()

        assert mgr.get_all_rule_keys_and_names() == [("t1", "新名称")]

    def test_create_rule(self, tmp_path):
        mgr = TuningRuleManager(rules_dir=tmp_path)
        mgr.create_rule("new_rule", "新规则")
        assert (tmp_path / "new_rule.yaml").exists()
        rule = mgr.get_rule("new_rule")
        assert rule.name == "新规则"
        assert rule.playstyles == {}
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

    def test_rename_rule(self, tmp_path):
        write_rule(tmp_path, minimal_rule())
        mgr = TuningRuleManager(rules_dir=tmp_path)
        mgr.rename_rule("t1", "t2")
        # 文件重命名 + data 内 key 同步更新
        assert not (tmp_path / "t1.yaml").exists()
        assert (tmp_path / "t2.yaml").exists()
        assert mgr.get_rule("t1") is None
        rule = mgr.get_rule("t2")
        assert rule.key == "t2"
        assert rule.name == "测试规则"

    def test_user_rename_system_rule_is_rejected_before_write(self, tmp_path):
        system = tmp_path / "system"
        local = tmp_path / "local"
        system.mkdir()
        write_rule(system, minimal_rule())
        mgr = TuningRuleManager(rules_dir=system)
        mgr._resolver = ConfigResolver(
            system_dir=system, local_dir=local, dev_mode=False,
        )
        mgr.reload()

        with pytest.raises(SystemContentProtected):
            mgr.rename_rule("t1", "t2")

        assert not (local / "t2.yaml").exists()
        assert mgr.get_rule("t1") is not None
        assert mgr.get_rule("t2") is None

    def test_rename_same_key_noop(self, tmp_path):
        write_rule(tmp_path, minimal_rule())
        mgr = TuningRuleManager(rules_dir=tmp_path)
        mgr.rename_rule("t1", "t1")  # 幂等
        assert (tmp_path / "t1.yaml").exists()

    @pytest.mark.parametrize("old,new", [
        ("nope", "t2"),          # 旧 key 未注册
        ("t1", "BadKey"),        # 新 key 非法
        ("t1", ""),              # 空 key
    ])
    def test_rename_invalid(self, tmp_path, old, new):
        write_rule(tmp_path, minimal_rule())
        mgr = TuningRuleManager(rules_dir=tmp_path)
        with pytest.raises(RuleValidationError):
            mgr.rename_rule(old, new)

    def test_rename_to_existing_rejected(self, tmp_path):
        write_rule(tmp_path, minimal_rule())
        write_rule(tmp_path, minimal_rule(key="t2", name="另一规则"),
                   name="t2.yaml")
        mgr = TuningRuleManager(rules_dir=tmp_path)
        with pytest.raises(RuleValidationError):
            mgr.rename_rule("t1", "t2")


# ─── 规则可引用词表守护 ────────────────────────────────────────

class TestStandardAffixNames:
    def test_rule_affix_names_are_standard(self):
        # 规则内出现的词条名必须在规则可引用词表内（标准词条
        # 全集 + 动态词条），否则判定/校验对不上
        candidates = set(rule_affix_candidates())
        for rule in get_tuning_rules().values():
            unknown = rule.referenced_affixes() - candidates
            assert not unknown, f"{rule.key} 存在非法词条名: {unknown}"

    def test_candidates_include_specific_attrs(self):
        # 候选词表 = 标准词条全集（含 8 个具体属攻）+ 4 个动态词条
        candidates = rule_affix_candidates()
        specific = specific_attr_names()
        assert len(specific) == 8
        assert set(specific) <= set(candidates)
        assert set(DYNAMIC_AFFIXES) <= set(candidates)
        assert (set(candidates)
                == set(standard_affix_names()) | set(DYNAMIC_AFFIXES))

    def test_dynamic_affixes_follow_wuxiang(self):
        # 动态词条插在最小无相攻击之后（价值语境相邻）
        candidates = rule_affix_candidates()
        at = candidates.index("最小无相攻击")
        assert candidates[at + 1:at + 5] == list(DYNAMIC_AFFIXES)


# ─── 属攻→动态词条归类 ───────────────────────────────

class TestDynamicAffixMap:
    def test_generic_and_empty_return_empty(self):
        # 通用/空 不做任何归类（混搭流保持字面匹配）
        assert dynamic_affix_map("通用") == {}
        assert dynamic_affix_map("") == {}

    def test_specific_maps_to_dynamic(self):
        # 裂石视角：本属→最大/最小本属攻击，其余属性→最大/最小
        # 外属攻击（多对一）
        eq = dynamic_affix_map("裂石")
        assert eq["最大裂石攻击"] == "最大本属攻击"
        assert eq["最小裂石攻击"] == "最小本属攻击"
        assert eq["最大破竹攻击"] == "最大外属攻击"
        assert eq["最小牵丝攻击"] == "最小外属攻击"
        # 无相词条不参与归类（字面语义，仅武器掉落）
        assert "最大无相攻击" not in eq
        assert "最小无相攻击" not in eq
        # 映射源覆盖全部具体属攻，目标均为动态词条
        assert set(eq) == set(specific_attr_names())
        assert set(eq.values()) == set(DYNAMIC_AFFIXES)

    def test_attr_candidates_include_generic_first(self):
        attrs = standard_playstyle_attrs()
        assert attrs[0] == "通用"
        assert "裂石" in attrs and "鸣金" in attrs and "牵丝" in attrs


# ─── 基础配置 tune_config ─────────────────────────

def _valid_config() -> dict:
    thresholds = {p: ["gold"] for p in QUALITY_PARTS}
    thresholds["冠胄"] = ["gold", "purple"]
    return {
        "base_rules": ["default"],
        "quality_thresholds": thresholds,
        "switches": {"keep_pvp": {"name": "保留PVP装备"}},
    }


class TestTuneConfig:
    def test_builtin_config_loaded(self):
        config = get_tune_config()
        # base_rules 非空
        assert "default" in config.base_rules
        # 品阶门槛锁死为固定 7 个标准部位
        assert list(config.quality_thresholds) == list(QUALITY_PARTS)
        # 开关注册表含 keep_danti（保留单体奇术增）
        assert config.switches.get("keep_danti")

    def test_quality_ok_by_part(self):
        config = parse_tune_config(_valid_config())
        assert config.quality_ok("冠胄", "purple") is True
        assert config.quality_ok("冠胄", "blue") is False
        assert config.quality_ok("武器", "gold") is True
        assert config.quality_ok("武器", "purple") is False

    def test_quality_ok_rule_overrides(self):
        # 规则级覆盖：列出的部位优先，未列部位沿用全局
        config = parse_tune_config(_valid_config())
        overrides = {"佩": ["gold", "purple"]}
        assert config.quality_ok("佩", "purple", overrides) is True
        assert config.quality_ok("佩", "purple") is False
        assert config.quality_ok("环", "gold", overrides) is True
        assert config.quality_ok("环", "purple", overrides) is False

    def test_switches_parsed(self):
        config = parse_tune_config(_valid_config())
        assert config.switches == {"keep_pvp": "保留PVP装备"}

    def test_switches_optional(self):
        data = _valid_config()
        data.pop("switches")
        assert parse_tune_config(data).switches == {}

    @pytest.mark.parametrize("mutate", [
        lambda d: d["quality_thresholds"].pop("佩"),           # 缺少部位
        lambda d: d["quality_thresholds"].update({"default": ["gold"]}),  # 未知部位
        lambda d: d["quality_thresholds"].update({"武器": ["legendary"]}),  # 非法品阶
    ])
    def test_quality_threshold_rejected(self, mutate):
        data = _valid_config()
        mutate(data)
        with pytest.raises(RuleValidationError):
            parse_tune_config(data)

    def test_pvp_section_rejected(self):
        # 旧版 pvp 段已废弃，出现即报错提示新写法
        data = _valid_config()
        data["pvp"] = {"names": ["单体类奇术增伤"]}
        with pytest.raises(RuleValidationError, match="switches"):
            parse_tune_config(data)

    @pytest.mark.parametrize("legacy", ["min_level", "materials", "behavior"])
    def test_legacy_sections_rejected(self, legacy):
        # 0.1 预览版硬拒绝：旧段已迁移至 base_groups/*.yaml
        data = _valid_config()
        data[legacy] = {}
        with pytest.raises(RuleValidationError, match="迁移"):
            parse_tune_config(data)

    @pytest.mark.parametrize("switches", [
        {"BadKey": {"name": "非法大写"}},
        {"1abc": {"name": "数字开头"}},
        {"keep_pvp": {"name": ""}},      # name 不能为空
        {"keep_pvp": "保留PVP装备"},      # spec 必须是 dict
    ])
    def test_bad_switches_rejected(self, switches):
        data = _valid_config()
        data["switches"] = switches
        with pytest.raises(RuleValidationError):
            parse_tune_config(data)


# ─── 材料设置 materials ─────────────────────────

class TestMaterialSettings:
    def test_defaults_when_section_missing(self):
        # materials 段缺省 → 全部空默认值（无狗粮规则）
        m = parse_tuning_group(_valid_group()).materials
        assert m.stone_check_enabled is False
        assert m.stone_min_count == 100
        assert m.stone_insufficient_action == "abort"
        assert m.food_rules == []

    def test_full_section_parsed(self):
        data = _valid_group()
        data["materials"] = {
            "stone_check": {"enabled": True, "min_count": 500,
                            "insufficient_action": "ask"},
            "food_rules": [
                {"pct": 95, "min_expect": "top", "min_quality": "gold",
                 "food": "彩狗粮", "on_insufficient": "skip"},
                {"pct": 0, "min_expect": "normal", "min_quality": "purple",
                 "food": "紫狗粮"},
                {"food": ""},                     # 终止规则：命中即不添加
            ],
        }
        m = parse_tuning_group(data).materials
        assert m.stone_check_enabled is True
        assert m.stone_min_count == 500
        assert m.stone_insufficient_action == "ask"
        assert m.food_rules == [
            FoodRule(pct=95, min_expect="top", min_quality="gold",
                     food="彩狗粮", on_insufficient="skip"),
            FoodRule(pct=0, min_expect="normal", min_quality="purple",
                     food="紫狗粮"),
            FoodRule(),
        ]

    def test_empty_rules_legal(self):
        # 空列表合法 = 从不添加狗粮
        data = _valid_group()
        data["materials"] = {"food_rules": []}
        assert parse_tuning_group(data).materials.food_rules == []

    def test_legacy_food_strategy_rejected(self):
        # 旧 food_strategy 段已废弃，出现即报错提示新写法
        data = _valid_group()
        data["materials"] = {"food_strategy": {"high_pct": 90}}
        with pytest.raises(RuleValidationError, match="已废弃"):
            parse_tuning_group(data)

    def test_builtin_group_materials_loaded(self):
        # 内置 default.yaml 的 materials 段可正常解析
        m = get_tuning_group("default").materials
        assert isinstance(m, MaterialSettings)
        assert all(isinstance(r, FoodRule) for r in m.food_rules)

    @pytest.mark.parametrize("materials", [
        ["not", "a", "dict"],                          # 段须为 dict
        {"stone_check": "yes"},                        # 子段须为 dict
        {"stone_check": {"min_count": 0}},             # 低于下界
        {"stone_check": {"min_count": "100"}},         # 字符串伪整数
        {"stone_check": {"min_count": True}},          # bool 伪装 int
        {"stone_check": {"insufficient_action": "quit"}},  # 不足处理非法
        {"stone_check": {"insufficient_action": "continue"}},  # 狗粮枚举不通用
        {"food_rules": {"pct": 90}},                    # 须为 list
        {"food_rules": ["金狗粮"]},                     # 元素须为 dict
        {"food_rules": [{"pct": 101}]},                 # 超出上界
        {"food_rules": [{"pct": True}]},                # bool 伪装 int
        {"food_rules": [{"min_expect": "junk"}]},       # 期望档位非法
        {"food_rules": [{"min_quality": "green"}]},     # 品阶非法
        {"food_rules": [{"food": "神狗粮"}]},           # 非法 label
        {"food_rules": [{"on_insufficient": "abort"}]},  # 行为非法
    ])
    def test_bad_materials_rejected(self, materials):
        data = _valid_group()
        data["materials"] = materials
        with pytest.raises(RuleValidationError):
            parse_tuning_group(data)


# ─── 行为配置 behavior ─────────────────────

def _rating(value: str | None):
    """固定评级提供者（忽略判定语义）"""
    return lambda _scope, _keys, _fao=False: value


class TestBehaviorSettings:
    def test_defaults_when_section_missing(self):
        # scan/tune 段缺省 → scan 默认启用（门槛 excellent），
        # tune 默认关，max_resets 取游戏硬限
        g = parse_tuning_group(_valid_group())
        assert g.scan.enabled is True and g.scan.rules == []
        assert g.scan.entry_min_rating == "excellent"
        assert g.tune.enabled is False and g.tune.rules == []
        assert g.tune.max_resets == MAX_TUNE_RESETS
        assert g.tune.reset_exhausted_action == "skip"

    def test_full_section_parsed(self):
        data = _valid_group()
        data["scan"] = {
            "enabled": True,
            "entry_min_rating": "top",
            "rules": [
                {"parts": ["武器"], "max_quality": "blue",
                 "max_pct": 100, "max_rating": "junk",
                 "judge_scope": "custom",
                 "judge_rules": ["huiyi_general", "heal_pure"],
                 "first_affix_only": True,
                 "action": "recycle"},
            ],
        }
        data["tune"] = {
            "enabled": True,
            "rules": [
                {"enabled": False, "max_rating": "junk", "judge_scope": "all",
                 "action": "recycle"},
                {"max_pct": 30, "action": "reset"},
                {"max_rating": "normal", "action": "skip"},
                {"action": "continue"},
            ],
            "max_resets": 2,
            "reset_exhausted_action": "recycle",
        }
        g = parse_tuning_group(data)
        assert g.scan.enabled is True
        assert g.scan.entry_min_rating == "top"
        assert g.scan.rules == [BehaviorRule(
            parts=["武器"], max_quality="blue", ratings=["junk"],
            judge_scope="custom",
            judge_rules=["huiyi_general", "heal_pure"],
            first_affix_only=True, action="recycle")]
        assert g.tune.enabled is True
        # 判定语义逐规则声明，缺省 incoming
        assert [r.judge_scope for r in g.tune.rules] == [
            "all", "incoming", "incoming", "incoming"]
        assert [r.action for r in g.tune.rules] == [
            "recycle", "reset", "skip", "continue"]
        assert [r.enabled for r in g.tune.rules] == [False, True, True, True]
        assert g.tune.max_resets == 2
        assert g.tune.reset_exhausted_action == "recycle"

    def test_stage_missing_defaults(self):
        # 只声明 tune → scan 取默认（启用/excellent）
        data = _valid_group()
        data["tune"] = {"enabled": True}
        g = parse_tuning_group(data)
        assert g.tune.enabled is True and g.tune.rules == []
        assert g.scan.enabled is True
        assert g.scan.entry_min_rating == "excellent"

    def test_legacy_recycle_rejected(self):
        # 旧 recycle 段已废弃，出现即报错提示新写法
        data = _valid_config()
        data["recycle"] = {"scan": {"enabled": True}}
        with pytest.raises(RuleValidationError, match="behavior"):
            parse_tune_config(data)

    @pytest.mark.parametrize("scan", [
        ["not", "a", "dict"],                            # 段须为 dict
        {"entry_min_rating": "good"},                    # 门槛档位非法
        {"judge_scope": "incoming"},                     # 段级语义已废弃
        {"first_affix_only": True},                      # 段级仅首词条已废弃
        {"rules": [{"action": "recycle",
                      "judge_scope": "mixed"}]},         # 判定语义非法
        {"rules": [{"action": "recycle",
                      "judge_scope": "incoming",
                      "judge_rules": ["huiyi_general"]}]},  # 非 custom 带自选
        {"rules": [{"action": "recycle",
                      "judge_scope": "custom",
                      "judge_rules": "huiyi"}]},         # 须为 list
        {"rules": [{"action": "recycle",
                      "judge_scope": "custom",
                      "judge_rules": ["BadKey"]}]},      # key 格式非法
        {"rules": {"action": "recycle"}},                # rules 须为 list
        {"rules": ["回收"]},                             # 元素须为 dict
        {"rules": [{}]},                                 # action 必填
        {"rules": [{"action": "continue"}]},             # scan 无 continue
        {"rules": [{"action": "reset"}]},                # scan 无 reset
        {"rules": [{"action": "recycle",
                      "parts": ["魅力"]}]},              # 未知部位
        {"rules": [{"action": "recycle",
                      "max_quality": "green"}]},         # 品阶非法
        {"rules": [{"action": "recycle",
                      "max_pct": 101}]},                 # 超出上界
        {"rules": [{"action": "recycle",
                      "max_pct": True}]},                # bool 伪装 int
        {"rules": [{"action": "recycle",
                      "pct_op": "gt"}]},                 # 比较方向非法
        {"rules": [{"action": "recycle",
                      "pct": 101}]},                     # pct 超出上界
        {"rules": [{"action": "recycle",
                      "max_rating": "good"}]},           # 评级非法（历史字段）
        {"rules": [{"action": "recycle",
                      "ratings": ["good"]}]},            # 评级档位非法
        {"rules": [{"action": "recycle",
                      "ratings": "junk"}]},              # ratings 须为 list
        {"rules": [{"action": "recycle",
                      "judge_scope": "affix"}]},         # 自选词条 ratings 禁止为空
        {"rules": [{"action": "recycle",
                      "judge_scope": "affix",
                      "ratings": []}]},                  # 空 list 同报错
        {"rules": [{"action": "recycle",
                      "judge_scope": "affix",
                      "ratings": ["不存在的词条"]}]},    # 词条须在词表内
        {"rules": [{"action": "recycle",
                      "judge_scope": "affix",
                      "ratings": ["最大外功攻击"],
                      "judge_rules": ["huiyi_general"]}]},  # affix 不可声明自选规则
    ])
    def test_bad_scan_rejected(self, scan):
        data = _valid_group()
        data["scan"] = scan
        with pytest.raises(RuleValidationError):
            parse_tuning_group(data)

    @pytest.mark.parametrize("tune", [
        ["not", "a", "dict"],                            # 段须为 dict
        {"judge_rules": []},                             # 段级自选已废弃
        {"rules": [{"action": "skip",
                      "first_affix_only": True}]},       # 仅首词条仅扫描处置表可声明
        {"rules": [{"action": "skip",
                      "judge_scope": "all",
                      "judge_rules": ["huiyi_general"]}]},  # 非 custom 带自选
        {"max_resets": 4},                               # 超游戏硬限
        {"max_resets": "3"},                             # 字符串伪整数
        {"reset_exhausted_action": "reset"},             # 转处置非法
    ])
    def test_bad_tune_rejected(self, tune):
        data = _valid_group()
        data["tune"] = tune
        with pytest.raises(RuleValidationError):
            parse_tuning_group(data)

    def test_scan_decide_first_hit(self):
        # 处置表自上而下首条命中；未启用/无命中 → skip 跳过
        junk = _rating("junk")
        data = _valid_group()
        data["scan"] = {"enabled": True, "rules": [
            {"max_pct": 30, "action": "recycle"},
            {"max_quality": "purple", "action": "skip"},
        ]}
        scan = parse_tuning_group(data).scan
        # 首条命中即生效：cap 20 ≤ 30 → 回收（不再看后续）
        assert scan.decide("武器", "gold", 20, junk)[0] == "recycle"
        # 首条不中、次条 ≤紫色 命中（蓝色 ≤ 紫色）→ 跳过该装备
        assert scan.decide("武器", "blue", 50, junk)[0] == "skip"
        # 金色超出 ≤紫色 → 全部不命中 = 默认跳过
        assert scan.decide("武器", "gold", 50, junk)[0] == "skip"
        # max_pct 限制下 cap_pct 识别失败视为不达标（保守不回收）
        assert scan.decide("武器", "gold", None, junk)[0] == "skip"
        # 未启用 → 一律跳过
        data["scan"]["enabled"] = False
        disabled = parse_tuning_group(data).scan
        assert disabled.decide("武器", "gold", 20, junk)[0] == "skip"

    def test_disabled_behavior_rule_is_skipped(self):
        scan = ScanBehavior(enabled=True, rules=[
            BehaviorRule(enabled=False, action="recycle"),
            BehaviorRule(action="skip"),
        ])
        assert scan.decide("武器", "gold", 50, _rating("junk"))[0] == "skip"

    def test_rule_judge_semantics_lazy(self):
        # 评级按各规则自身判定语义懒取：不限评级的规则不取评级；
        # 同一装备不同语义可得不同评级（传入判垃圾、自选判顶级）
        calls: list[tuple[str, list[str]]] = []

        def rating_of(scope, keys, _fao=False):
            calls.append((scope, keys))
            return "top" if scope == "custom" else "junk"

        data = _valid_group()
        data["scan"] = {"enabled": True, "rules": [
            {"max_rating": "junk", "judge_scope": "custom",
             "judge_rules": ["huiyi_general"], "action": "recycle"},
            {"max_rating": "junk", "action": "recycle"},
            {"action": "skip"},
        ]}
        scan = parse_tuning_group(data).scan
        # 规则1 自选判顶级不命中；规则2 传入判垃圾命中 → 回收
        assert scan.decide("武器", "gold", 90, rating_of)[0] == "recycle"
        assert calls == [("custom", ["huiyi_general"]), ("incoming", [])]
        # 不限评级的规则不取评级（前两条部位不限仍需取）
        calls.clear()
        top_of = _rating("top")
        assert scan.decide("武器", "gold", 90, top_of)[0] == "skip"

    def test_purple_only_quality(self):
        # purple_only 为精确档：仅紫色命中，金/蓝不命中
        data = _valid_group()
        data["scan"] = {"enabled": True, "rules": [
            {"max_quality": "purple_only", "action": "recycle"},
        ]}
        scan = parse_tuning_group(data).scan
        junk = _rating("junk")
        assert scan.decide("武器", "purple", 50, junk)[0] == "recycle"
        assert scan.decide("武器", "gold", 50, junk)[0] == "skip"
        assert scan.decide("武器", "blue", 50, junk)[0] == "skip"

    def test_affix_scope_parsed_and_decide(self):
        # 自选词条语义：ratings 存词条名（去重），不跑潜力判定，
        # 按装备词条名匹配；pct/品阶条件仍参与 AND
        calls: list[str] = []

        def rating_of(scope, keys, _fao=False):
            calls.append(scope)
            return "junk"

        data = _valid_group()
        data["scan"] = {"enabled": True, "rules": [
            {"parts": ["武器"], "max_quality": "purple_only",
             "judge_scope": "affix",
             "ratings": ["最大鸣金攻击", "最大外功攻击",
                         "最大鸣金攻击"],
             "pct_op": "ge", "pct": 90, "action": "skip"},
            {"action": "recycle"},
        ]}
        scan = parse_tuning_group(data).scan
        # 去重后剩两项（词表序归一）
        assert len(scan.rules[0].ratings) == 2
        assert set(scan.rules[0].ratings) == {
            "最大鸣金攻击", "最大外功攻击"}
        # 紫武器含目标词条 + 首词条 ≥90 → 命中跳过（不回收）
        assert scan.decide("武器", "purple", 95, rating_of,
                           ["最大鸣金攻击",
                            "最小外功攻击"])[0] == "skip"
        assert calls == []  # affix 语义不跑潜力判定
        # 首词条 pct 不足 → 落回收
        assert scan.decide("武器", "purple", 80, rating_of,
                           ["最大鸣金攻击"])[0] == "recycle"
        # 金装超出 purple_only → 落回收
        assert scan.decide("武器", "gold", 95, rating_of,
                           ["最大鸣金攻击"])[0] == "recycle"
        # 装备无目标词条 → 落回收
        assert scan.decide("武器", "purple", 95, rating_of,
                           ["最小外功攻击"])[0] == "recycle"

    def test_affix_scope_first_affix_only(self):
        # 勾选仅首词条时只判定 affixes[0]：目标词非首不命中
        data = _valid_group()
        data["scan"] = {"enabled": True, "rules": [
            {"judge_scope": "affix", "ratings": ["最大外功攻击"],
             "first_affix_only": True, "action": "skip"},
            {"action": "recycle"},
        ]}
        scan = parse_tuning_group(data).scan
        junk = _rating("junk")
        assert scan.decide("武器", "purple", 50, junk,
                           ["最大外功攻击",
                            "最小外功攻击"])[0] == "skip"
        assert scan.decide("武器", "purple", 50, junk,
                           ["最小外功攻击",
                            "最大外功攻击"])[0] == "recycle"

    def test_tune_decide_defaults_and_full(self):
        # 无命中默认：未满 = 继续调律；词条满 = 结束保留；
        # full=True 时 continue 规则转为 skip，并终止后续规则判定
        junk, normal, top = (_rating("junk"), _rating("normal"),
                             _rating("top"))
        data = _valid_group()
        data["tune"] = {"enabled": True, "rules": [
            {"max_rating": "junk", "action": "recycle"},
            {"max_rating": "normal", "action": "continue"},
        ]}
        tune = parse_tuning_group(data).tune
        # 首条命中 → 回收（满/未满一致）
        assert tune.decide("武器", "gold", 95, junk, False)[0] == "recycle"
        assert tune.decide("武器", "gold", 95, junk, True)[0] == "recycle"
        # 次条 continue：未满命中生效；词条满跳过 → 默认跳过该装备
        assert tune.decide("武器", "gold", 95, normal,
                           False)[0] == "continue"
        assert tune.decide("武器", "gold", 95, normal, True)[0] == "skip"
        guarded = TuneBehavior(enabled=True, rules=[
            BehaviorRule(ratings=["top"], action="continue"),
            BehaviorRule(action="recycle"),
        ])
        assert guarded.decide("武器", "gold", 95, top, True)[0] == "skip"
        # 全部不命中 → 默认：未满继续、满跳过该装备
        assert tune.decide("武器", "gold", 95, top, False)[0] == "continue"
        assert tune.decide("武器", "gold", 95, top, True)[0] == "skip"
        # 未启用 → 同默认
        assert TuneBehavior().decide(
            "武器", "gold", 95, junk, False)[0] == "continue"
        assert TuneBehavior().decide(
            "武器", "gold", 95, junk, True)[0] == "skip"


class TestDecideFood:
    """decide_food 新语义：三条件顺序匹配 + 持有量判定 + 不足策略"""

    STOCKS = {"彩狗粮": 5, "金狗粮": 3, "紫狗粮": 0}

    def test_disabled_food_rule_is_skipped(self):
        rules = [
            FoodRule(enabled=False, food="彩狗粮"),
            FoodRule(food="金狗粮"),
        ]
        decision = MaterialSettings(food_rules=rules).decide_food(
            100, "top", "gold", self.STOCKS)
        assert decision.food == "金狗粮"
    # 测试用狗粮规则（与 default.yaml 中的示例一致，但非“默认”）
    _RULES = [
        FoodRule(pct=98, min_expect="top", food="彩狗粮"),
        FoodRule(pct=90, min_expect="excellent", food="金狗粮"),
    ]

    def test_first_rule_hit(self):
        # 规则1：首词条≥98 且期望≥顶级 → 彩狗粮
        m = MaterialSettings(food_rules=self._RULES)
        d = m.decide_food(98, "top", "gold", self.STOCKS)
        assert (d.action, d.food) == ("feed", "彩狗粮")

    def test_second_rule_hit(self):
        # 规则1 不命中（cap 92 < 98）→ 顺序落到规则2 金狗粮
        m = MaterialSettings(food_rules=self._RULES)
        d = m.decide_food(
            92, "excellent", "purple", self.STOCKS)
        assert (d.action, d.food) == ("feed", "金狗粮")

    def test_no_rule_hit(self):
        m = MaterialSettings(food_rules=self._RULES)
        d = m.decide_food(50, "top", "gold", self.STOCKS)
        assert (d.action, d.food) == ("none", "")

    def test_pct_zero_unlimited(self):
        # pct=0 不限首词条：cap_pct 识别失败（None）也可命中
        m = MaterialSettings(food_rules=[FoodRule(food="金狗粮")])
        d = m.decide_food(None, "normal", "blue", {"金狗粮": 1})
        assert (d.action, d.food) == ("feed", "金狗粮")

    def test_cap_pct_none_fails_positive_pct(self):
        # pct>0 时 cap_pct 识别失败视为不达标
        m = MaterialSettings(food_rules=self._RULES)
        d = m.decide_food(None, "top", "gold", self.STOCKS)
        assert d.action == "none"

    def test_expect_none_never_hits(self):
        # 无任何适用规则（expect=None）→ 期望条件永不命中
        m = MaterialSettings(food_rules=self._RULES)
        d = m.decide_food(98, None, "gold", self.STOCKS)
        assert d.action == "none"

    def test_quality_terminator_ordering(self):
        # 「品阶≥金→不添加」排在「品阶≥紫→紫狗粮」前：
        # 金不喂、紫喂紫、蓝全部不命中
        m = MaterialSettings(food_rules=[
            FoodRule(min_quality="gold"),
            FoodRule(min_quality="purple", food="紫狗粮"),
        ])
        stocks = {"紫狗粮": 9}
        assert m.decide_food(50, "normal", "gold", stocks).action == "none"
        d = m.decide_food(50, "normal", "purple", stocks)
        assert (d.action, d.food) == ("feed", "紫狗粮")
        assert m.decide_food(50, "normal", "blue", stocks).action == "none"

    def test_insufficient_continue_falls_through(self):
        # 命中但库存不足（读不到）→ continue 落到下一条
        m = MaterialSettings(food_rules=[
            FoodRule(food="彩狗粮"),
            FoodRule(food="金狗粮"),
        ])
        d = m.decide_food(50, "normal", "blue", {"金狗粮": 2})
        assert (d.action, d.food) == ("feed", "金狗粮")

    def test_insufficient_skip_aborts_equipment(self):
        m = MaterialSettings(food_rules=[
            FoodRule(food="彩狗粮", on_insufficient="skip"),
            FoodRule(food="金狗粮"),
        ])
        d = m.decide_food(50, "normal", "blue", {"金狗粮": 2})
        assert d.action == "skip"
        assert "跳过" in d.reason

    def test_zero_stock_is_insufficient(self):
        # 数量 0 与读不到同义：狗粮每轮只消耗一个，<1 即不足
        m = MaterialSettings(food_rules=[FoodRule(food="紫狗粮")])
        d = m.decide_food(50, "normal", "blue", {"紫狗粮": 0})
        assert d.action == "none"

    def test_empty_rules_never_feed(self):
        m = MaterialSettings(food_rules=[])
        d = m.decide_food(99, "top", "gold", {"彩狗粮": 9})
        assert d.action == "none"


# ─── 规则级品阶门槛覆盖 ─────────────────

class TestRuleQualityThresholds:
    def test_optional_and_subset_allowed(self):
        rule = parse_tuning_rule(minimal_rule())
        assert rule.quality_thresholds == {}
        rule = parse_tuning_rule(minimal_rule(
            quality_thresholds={"佩": ["gold", "purple"]}))
        assert rule.quality_thresholds == {"佩": ["gold", "purple"]}

    @pytest.mark.parametrize("thresholds", [
        {"default": ["gold"]},   # 未知部位
        {"佩": ["legendary"]},   # 非法品阶
    ])
    def test_quality_threshold_rejected(self, thresholds):
        with pytest.raises(RuleValidationError):
            parse_tuning_rule(minimal_rule(quality_thresholds=thresholds))

    def test_builtin_examples_loaded(self):
        # 内置示例：会意环 / 小外佩 金紫皆可
        rules = get_tuning_rules()
        assert rules["huiyi_general"].quality_thresholds == {
            "环": ["gold", "purple"]}
        assert rules["huixin_small"].quality_thresholds == {
            "佩": ["gold", "purple"]}


# ─── TuningGroupManager CRUD ─────────────────────

class TestTuningGroupManagerCRUD:
    """规则组 CRUD：新建空白 / 复制副本 / 删除（default 禁删）"""

    @pytest.fixture
    def mgr(self, tmp_path):
        """复制内置 base_groups/ 和 tune_config.yaml 到 tmp，构造独立管理器"""
        import shutil
        src = Path(__file__).parents[2] / "config" / "system" / "yysls" / "base_groups"
        for f in src.glob("*.yaml"):
            shutil.copy(f, tmp_path)
        # 同时复制 tune_config.yaml（base_rules 声明来源）
        # resolver 在测试模式下会查找 yysls/tune_config.yaml
        config_src = Path(__file__).parents[2] / "config" / "system" / "yysls" / "tune_config.yaml"
        yysls_dir = tmp_path / "yysls"
        yysls_dir.mkdir(exist_ok=True)
        shutil.copy(config_src, yysls_dir / "tune_config.yaml")
        return TuningGroupManager(groups_dir=tmp_path)

    def test_create_group_is_empty(self, mgr):
        # 新建组应全空（仅含 key/name），不带任何默认规则/材料
        mgr.create_group("test_empty", "测试空白")
        g = mgr.get_group("test_empty")
        assert g.key == "test_empty"
        assert g.name == "测试空白"
        assert g.scan.min_level == 100  # ScanBehavior 缺省值
        # 所有规则表应为空
        assert g.materials.food_rules == []
        assert g.scan.rules == []
        assert g.tune.rules == []

    @pytest.mark.parametrize("key,name,match", [
        ("BadKey", "非法大写", None),
        ("1abc", "数字开头", None),
        ("default", "重复", "已存在"),
        ("new_key", "", "名称"),
    ])
    def test_create_group_rejected(self, mgr, key, name, match):
        with pytest.raises(RuleValidationError, match=match):
            mgr.create_group(key, name)

    def test_copy_group_is_independent(self, mgr):
        # 复制组应是源组的独立副本
        mgr.copy_group("default", "default_copy", "默认副本")
        src = mgr.get_group("default")
        cp = mgr.get_group("default_copy")
        assert cp.name == "默认副本"
        assert cp.scan.min_level == src.scan.min_level
        assert cp.scan.rules == src.scan.rules
        # 修改副本不影响源组
        raw = mgr.get_raw("default_copy")
        raw["scan"]["min_level"] = 50
        mgr.save_group("default_copy", raw)
        assert mgr.get_group("default").scan.min_level != 50

    @pytest.mark.parametrize("method,args", [
        ("copy_group", ("nonexistent", "new", "新组")),
        ("delete_group", ("nonexistent",)),
    ])
    def test_nonexistent_rejected(self, mgr, method, args):
        with pytest.raises(RuleValidationError, match="不存在"):
            getattr(mgr, method)(*args)

    def test_delete_last_group_rejected(self, mgr):
        # 仅剩一个规则组时不可删除
        # 先删除其他组，直到只剩 default
        for key in list(mgr.get_groups()):
            if key != "default":
                mgr.delete_group(key)
        assert len(mgr.get_groups()) == 1
        with pytest.raises(RuleValidationError, match="至少"):
            mgr.delete_group("default")

    def test_delete_any_group_when_others_exist(self, mgr):
        # 有多个组时，任何组（包括 default）都可删除
        mgr.create_group("temp", "临时")
        assert "default" in mgr.get_groups()
        assert "temp" in mgr.get_groups()
        # 删除 default 也是允许的
        mgr.delete_group("default")
        assert "default" not in mgr.get_groups()
        assert "temp" in mgr.get_groups()
