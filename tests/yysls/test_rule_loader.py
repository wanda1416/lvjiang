"""调律规则加载器测试

覆盖 TuningRuleManager 的加载/排序/校验拒绝/保存与 get_raw 深拷贝、
create_rule/delete_rule、playstyles 节（含 attr）、4 条件原语与
条件组三种形态（单键 dict / list=AND / when+all 开关组）解析、
default_rating、tuning_base 开关注册表（switches），以及规则内
词条名与规则可引用词表（rule_affix_candidates：标准词条全集
+ 四个动态词条）的一致性守护。
"""

from pathlib import Path

import pytest
import yaml

from src.apps.yysls.evaluator import get_tuning_rules
from src.apps.yysls.evaluator.tuning_rules import (
    DYNAMIC_AFFIXES, QUALITY_PARTS, MaterialSettings, RuleValidationError,
    TuningRuleManager, dynamic_affix_map, get_tuning_base,
    get_tuning_rule_manager, parse_tuning_base, parse_tuning_rule,
    rule_affix_candidates, specific_attr_names, standard_affix_names,
    standard_playstyle_attrs,
)


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
        rules = get_tuning_rules()
        orders = [r.order for r in rules.values()]
        assert orders == sorted(orders)

    def test_required_fields_present(self):
        for rule in get_tuning_rules().values():
            assert rule.key and rule.name
            assert rule.affix_pool
            assert rule.patterns
            for pattern in rule.patterns.values():
                assert pattern.first

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
        registered = set(get_tuning_base().switches)
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


# ─── 创建与删除 ────────────────────────────────────────────

class TestCreateAndDelete:
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


# ─── 基础配置 tuning_base ─────────────────────────

def _valid_base() -> dict:
    thresholds = {p: ["gold"] for p in QUALITY_PARTS}
    thresholds["冠胄"] = ["gold", "purple"]
    return {
        "quality_thresholds": thresholds,
        "switches": {"keep_pvp": {"name": "保留PVP装备"}},
    }


class TestTuningBase:
    def test_builtin_base_loaded(self):
        base = get_tuning_base()
        # 品阶门槛锁死为固定 7 个标准部位
        assert list(base.quality_thresholds) == list(QUALITY_PARTS)
        # 开关注册表含 keep_pvp（保留PVP装备）
        assert base.switches.get("keep_pvp")

    def test_quality_ok_by_part(self):
        base = parse_tuning_base(_valid_base())
        assert base.quality_ok("冠胄", "purple") is True
        assert base.quality_ok("冠胄", "blue") is False
        assert base.quality_ok("武器", "gold") is True
        assert base.quality_ok("武器", "purple") is False

    def test_quality_ok_rule_overrides(self):
        # 规则级覆盖：列出的部位优先，未列部位沿用全局
        base = parse_tuning_base(_valid_base())
        overrides = {"佩": ["gold", "purple"]}
        assert base.quality_ok("佩", "purple", overrides) is True
        assert base.quality_ok("佩", "purple") is False
        assert base.quality_ok("环", "gold", overrides) is True
        assert base.quality_ok("环", "purple", overrides) is False

    def test_switches_parsed(self):
        base = parse_tuning_base(_valid_base())
        assert base.switches == {"keep_pvp": "保留PVP装备"}

    def test_switches_optional(self):
        data = _valid_base()
        data.pop("switches")
        assert parse_tuning_base(data).switches == {}

    def test_missing_part_rejected(self):
        data = _valid_base()
        data["quality_thresholds"].pop("佩")
        with pytest.raises(RuleValidationError):
            parse_tuning_base(data)

    def test_unknown_part_rejected(self):
        data = _valid_base()
        data["quality_thresholds"]["default"] = ["gold"]
        with pytest.raises(RuleValidationError):
            parse_tuning_base(data)

    def test_bad_quality_rejected(self):
        data = _valid_base()
        data["quality_thresholds"]["武器"] = ["legendary"]
        with pytest.raises(RuleValidationError):
            parse_tuning_base(data)

    def test_pvp_section_rejected(self):
        # 旧版 pvp 段已废弃，出现即报错提示新写法
        data = _valid_base()
        data["pvp"] = {"names": ["单体类奇术增伤"]}
        with pytest.raises(RuleValidationError, match="switches"):
            parse_tuning_base(data)

    @pytest.mark.parametrize("switches", [
        {"BadKey": {"name": "非法大写"}},
        {"1abc": {"name": "数字开头"}},
        {"keep_pvp": {"name": ""}},      # name 不能为空
        {"keep_pvp": "保留PVP装备"},      # spec 必须是 dict
    ])
    def test_bad_switches_rejected(self, switches):
        data = _valid_base()
        data["switches"] = switches
        with pytest.raises(RuleValidationError):
            parse_tuning_base(data)


# ─── 材料设置 materials ─────────────────────────

class TestMaterialSettings:
    def test_defaults_when_section_missing(self):
        # materials 段缺省 → 全部默认值（与原硬编码行为一致）
        m = parse_tuning_base(_valid_base()).materials
        assert m.stone_check_enabled is False
        assert m.stone_min_count == 100
        assert m.high_pct == 90
        assert m.high_pct_food == "金狗粮"
        assert m.quality_food == {"purple": "紫狗粮", "gold": ""}

    def test_full_section_parsed(self):
        data = _valid_base()
        data["materials"] = {
            "stone_check": {"enabled": True, "min_count": 500},
            "food_strategy": {
                "high_pct": 80,
                "high_pct_food": "彩狗粮",
                "quality_food": {"purple": "金狗粮", "gold": "紫狗粮"},
            },
        }
        m = parse_tuning_base(data).materials
        assert m.stone_check_enabled is True
        assert m.stone_min_count == 500
        assert m.high_pct == 80
        assert m.high_pct_food == "彩狗粮"
        assert m.quality_food == {"purple": "金狗粮", "gold": "紫狗粮"}

    def test_partial_quality_food_merges_defaults(self):
        # 只覆盖 gold → purple 沿用默认紫狗粮；空串=不加合法
        data = _valid_base()
        data["materials"] = {
            "food_strategy": {"quality_food": {"gold": "彩狗粮"}}}
        m = parse_tuning_base(data).materials
        assert m.quality_food == {"purple": "紫狗粮", "gold": "彩狗粮"}

    def test_builtin_base_materials_loaded(self):
        # 内置 tuning_base.yaml 的 materials 段可正常解析
        m = get_tuning_base().materials
        assert isinstance(m, MaterialSettings)
        assert m.high_pct_food in ("金狗粮", "紫狗粮", "彩狗粮")

    @pytest.mark.parametrize("materials", [
        ["not", "a", "dict"],                          # 段须为 dict
        {"stone_check": "yes"},                        # 子段须为 dict
        {"stone_check": {"min_count": 0}},             # 低于下界
        {"stone_check": {"min_count": "100"}},         # 字符串伪整数
        {"stone_check": {"min_count": True}},          # bool 伪装 int
        {"food_strategy": {"high_pct": 101}},          # 超出上界
        {"food_strategy": {"high_pct_food": "神狗粮"}},  # 非法 label
        {"food_strategy": {"high_pct_food": ""}},      # 高档狗粮不可为空
        {"food_strategy": {"quality_food": {"blue": "紫狗粮"}}},   # 未知品阶
        {"food_strategy": {"quality_food": {"purple": "神狗粮"}}},  # 非法 label
        {"food_strategy": {"quality_food": ["紫狗粮"]}},  # 须为 dict
    ])
    def test_bad_materials_rejected(self, materials):
        data = _valid_base()
        data["materials"] = materials
        with pytest.raises(RuleValidationError):
            parse_tuning_base(data)

    def test_decide_food_high_pct(self):
        m = MaterialSettings()
        food, reason = m.decide_food(90, "purple")
        assert food == "金狗粮"
        assert "90%" in reason

    def test_decide_food_by_quality(self):
        m = MaterialSettings()
        assert m.decide_food(50, "purple")[0] == "紫狗粮"
        food, reason = m.decide_food(50, "gold")
        assert food == ""                      # 金色默认不加
        assert "不添加" in reason

    def test_decide_food_unknown_quality(self):
        m = MaterialSettings()
        food, reason = m.decide_food(50, "blue")
        assert food == ""
        assert "保守" in reason
        # cap_pct 缺失（OCR 失败）也走保守分支
        assert m.decide_food(None, None)[0] == ""

    def test_decide_food_custom_config(self):
        m = MaterialSettings(high_pct=80, high_pct_food="彩狗粮",
                             quality_food={"gold": "金狗粮"})
        assert m.decide_food(85, "gold")[0] == "彩狗粮"
        assert m.decide_food(50, "gold")[0] == "金狗粮"
        # 品阶未配置（purple 被自定义配置移除）→ 保守不加
        assert m.decide_food(50, "purple")[0] == ""


# ─── 规则级品阶门槛覆盖 ─────────────────

class TestRuleQualityThresholds:
    def test_optional_and_subset_allowed(self):
        rule = parse_tuning_rule(minimal_rule())
        assert rule.quality_thresholds == {}
        rule = parse_tuning_rule(minimal_rule(
            quality_thresholds={"佩": ["gold", "purple"]}))
        assert rule.quality_thresholds == {"佩": ["gold", "purple"]}

    def test_unknown_part_rejected(self):
        with pytest.raises(RuleValidationError):
            parse_tuning_rule(minimal_rule(
                quality_thresholds={"default": ["gold"]}))

    def test_bad_quality_rejected(self):
        with pytest.raises(RuleValidationError):
            parse_tuning_rule(minimal_rule(
                quality_thresholds={"佩": ["legendary"]}))

    def test_builtin_examples_loaded(self):
        # 内置示例：会意环 / 小外佩 金紫皆可
        rules = get_tuning_rules()
        assert rules["huiyi_general"].quality_thresholds == {
            "环": ["gold", "purple"]}
        assert rules["huixin_small"].quality_thresholds == {
            "佩": ["gold", "purple"]}
