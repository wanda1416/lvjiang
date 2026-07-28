"""调律规则加载器测试

覆盖 TuningRuleManager 的加载/排序/校验拒绝/保存与 get_raw 深拷贝、
create_rule/delete_rule、playstyles 节（含 attr）与三档条件组语法解析，
以及规则内词条名与标准词条全集（attributes.yaml 普通词组
_aliases）的一致性守护。
"""

from pathlib import Path

import pytest
import yaml

from src.apps.yysls.evaluator import get_tuning_rules
from src.apps.yysls.evaluator.tuning_rules import (
    PVP_NAMES, QUALITY_PARTS, RuleValidationError, TuningRuleManager,
    attr_equivalence, get_tuning_base, get_tuning_rule_manager,
    parse_tuning_base, parse_tuning_rule, standard_affix_names,
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
                "junk_conditions": [{"not_contains": ["劲"]}],
                "top_conditions": [
                    [{"contains_all": ["劲"]},
                     {"not_contains": ["最大外功攻击"]}],
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
            "huiyi_general", "lieshi_small", "lieshi_big",
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
        assert len(pattern.junk_conditions[0]) == 1  # 单条件组
        assert len(pattern.top_conditions) == 1
        assert len(pattern.top_conditions[0]) == 2   # 组内 AND
        assert pattern.usable_conditions == []

    @pytest.mark.parametrize("mutate", [
        # 缺少必填字段 key/name
        lambda d: d.pop("key"),
        # 旧版 schema 字段不再支持
        lambda d: d.update(variants={"default": {}}),
        lambda d: d.update(sub_schools={"lieshi": {"name": "裂石"}}),
        lambda d: d.update(optional_pool=["劲"]),
        lambda d: d.update(junk_rules=[{"not_contains": ["劲"]}]),
        lambda d: d.update(has_keep_pvp=True),
        # affix_pool 词条不在标准词条全集
        lambda d: d["affix_pool"].append("大外"),
        # first 不能为空
        lambda d: d["patterns"]["环"].update(first=[]),
        # not_together 须恰好 2 个词条
        lambda d: d["patterns"]["环"].update(
            top_conditions=[{"not_together": ["最大外功攻击", "劲", "敏"]}]),
        # 未知条件原语
        lambda d: d["patterns"]["环"].update(
            top_conditions=[{"unknown_kind": ["最大外功攻击"]}]),
        # 条件组必须是 dict 或 dict 列表
        lambda d: d["patterns"]["环"].update(junk_conditions=["oops"]),
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


# ─── 标准词条名守护 ────────────────────────────────────────

class TestStandardAffixNames:
    def test_rule_affix_names_are_standard(self):
        # 规则内出现的词条名必须在标准词条全集内，
        # 否则判定时与 OCR 解析结果对不上
        standard = set(standard_affix_names())
        for rule in get_tuning_rules().values():
            used: set[str] = set()
            used.update(rule.transmute_priority)
            used.update(rule.affix_pool)
            for wr in rule.playstyles.values():
                for side in (wr.main, wr.sub):
                    if side.damage:
                        used.add(side.damage)
            for pattern in rule.patterns.values():
                used.update(pattern.first)
                for tier in (pattern.junk_conditions,
                             pattern.usable_conditions,
                             pattern.top_conditions):
                    for group in tier:
                        for cond in group:
                            used.update(cond.symbols)
            unknown = used - standard
            assert not unknown, f"{rule.key} 存在非标准词条名: {unknown}"

    def test_pvp_names_are_standard(self):
        assert PVP_NAMES <= set(standard_affix_names())


# ─── 属攻→无相等价 ───────────────────────────────

class TestAttrEquivalence:
    def test_generic_and_empty_return_empty(self):
        # 通用/空/None 无需转换
        assert attr_equivalence("通用") == {}
        assert attr_equivalence("") == {}

    def test_specific_maps_to_wuxiang(self):
        # 裂石的最大/最小属攻 → 通用无相攻击（按声明序对齐）
        eq = attr_equivalence("裂石")
        assert eq["最大裂石攻击"] == "最大无相攻击"
        assert eq["最小裂石攻击"] == "最小无相攻击"
        # 目标均为标准词条
        standard = set(standard_affix_names())
        assert set(eq.values()) <= standard

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
        "pvp": {
            "names": ["单体类奇术增伤", "对玩家单位增效"],
            "substitutions": {
                "胫甲": {"对玩家单位增效": "对首领单位增伤"},
                "冠胄": {"add_to_pool": ["单体类奇术增伤"]},
            },
        },
    }


class TestTuningBase:
    def test_builtin_base_loaded(self):
        base = get_tuning_base()
        # 品阶门槛锁死为固定 7 个标准部位
        assert list(base.quality_thresholds) == list(QUALITY_PARTS)
        assert base.pvp_names  # 非空

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

    def test_pvp_part_rule_parsed(self):
        base = parse_tuning_base(_valid_base())
        assert base.pvp_parts["胫甲"].substitutions == {
            "对玩家单位增效": "对首领单位增伤"}
        assert base.pvp_parts["冠胄"].add_to_pool == ["单体类奇术增伤"]

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

    def test_unknown_pvp_part_rejected(self):
        data = _valid_base()
        data["pvp"]["substitutions"]["鞋子"] = {"add_to_pool": ["劲"]}
        with pytest.raises(RuleValidationError):
            parse_tuning_base(data)


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
        assert rules["lieshi_small"].quality_thresholds == {
            "佩": ["gold", "purple"]}
