"""调律规则加载器测试

覆盖 TuningRuleManager 的加载/排序/校验拒绝/保存与 get_raw 深拷贝、
create_rule/delete_rule、playstyles 节（含 attr）、4 条件原语与
条件组三种形态（单键 dict / list=AND / when+all 开关组）解析、
default_rating、tune_config 开关注册表（switches），以及规则内
词条名与规则可引用词表（rule_affix_candidates：标准词条全集
+ 四个动态词条）的一致性守护。
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

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
    get_tuning_group,
    get_tuning_rule_manager,
    parse_tune_config,
    parse_tuning_group,
    parse_tuning_rule,
    rule_affix_candidates,
    specific_attr_names,
    standard_affix_names,
)
from lvjiang.core.config import versioning
from lvjiang.core.config.resolver import ConfigResolver, SystemContentProtected
from tests.case_matrix import case_matrix


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


def test_behavior_rule_summary_condenses_all_parts():
    summary = BehaviorRule(parts=list(QUALITY_PARTS)).summary()
    assert summary.startswith("全部 且 ")
    assert "/".join(QUALITY_PARTS) not in summary


@case_matrix("all_value", ["全部", "All", "- All -", "all"])
def test_behavior_rule_all_parts_is_language_independent(all_value):
    data = _valid_group()
    data["scan"] = {
        "rules": [{"action": "recycle", "parts": [all_value]}],
    }

    group = parse_tuning_group(data)

    assert group.scan.rules[0].parts == list(QUALITY_PARTS)


def test_behavior_rule_normalizes_english_part_values():
    data = _valid_group()
    data["scan"] = {
        "rules": [{
            "action": "recycle",
            "parts": ["Weapon", "Head Guard", "Wrist Guard"],
        }],
    }

    group = parse_tuning_group(data)

    assert group.scan.rules[0].parts == ["武器", "冠胄", "腕甲"]


def test_tuning_rule_normalizes_english_part_keys():
    data = minimal_rule(quality_thresholds={"Weapon": ["gold"]})
    data["patterns"] = {"Ring": data["patterns"]["环"]}

    rule = parse_tuning_rule(data)

    assert list(rule.patterns) == ["环"]
    assert rule.quality_thresholds == {"武器": ["gold"]}


def test_builtin_groups_load_after_english_i18n_cold_start():
    """语言必须先于业务模块初始化，模拟真实英文冷启动的导入顺序。"""
    project_root = Path(__file__).parents[2]
    env = os.environ.copy()
    source_root = str(project_root / "src")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (
        source_root,
        env.get("PYTHONPATH", ""),
    )))
    script = """
from pathlib import Path
import yaml

from lvjiang.i18n import init_i18n, load_app_i18n

init_i18n("en_US")
load_app_i18n("yysls")

from lvjiang.apps.yysls.config import (
    AFFIX_CATEGORY_NAMES,
    EQUIP_PART_NAMES,
    get_game_config,
)
from lvjiang.apps.yysls import hooks
from lvjiang.apps.yysls.core.equip_parser.constants import infer_part
from lvjiang.apps.yysls.core.evaluator.base import Rating
from lvjiang.apps.yysls.core.tuning_rules import QUALITY_PARTS, parse_tuning_group

assert EQUIP_PART_NAMES == ("武器", "环", "佩", "冠胄", "胸甲", "胫甲", "腕甲")
assert QUALITY_PARTS == EQUIP_PART_NAMES
assert AFFIX_CATEGORY_NAMES[0] == "外功类"
assert hooks.id == "yysls" and hooks.name != "燕云十六声"
assert infer_part("环") == "环"
assert Rating.TOP.value == "顶级"
assert get_game_config().get_type_to_group()["环"] == "ring"
assert get_game_config().get_group_to_part()["wrist"] == "腕甲"
for name in ("default.yaml", "aggressive.yaml"):
    path = Path("config/system/yysls/base_groups") / name
    parse_tuning_group(yaml.safe_load(path.read_text(encoding="utf-8")))
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=project_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr


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


    def test_huixin_small_common_condition_policy(self):
        """会心小外的通用垃圾/一般判定保持当前业务口径。"""
        raw = get_tuning_rule_manager().get_raw("huixin_small")
        assert raw["common_conditions"] == {
            "junk_conditions": [[
                {"count_min": {
                    "symbols": ["最小本属攻击", "最小外属攻击"],
                    "min": 2,
                }},
                {"count_min": {
                    "symbols": ["精准率", "会心率"],
                    "min": 1,
                }},
            ]],
            "normal_conditions": [
                {"count_min": {
                    "symbols": ["最小本属攻击", "最小外属攻击"],
                    "min": 2,
                }},
                {"count_max": {
                    "symbols": ["最小外功攻击"],
                    "max": 0,
                }},
            ],
        }

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


# ─── schema 校验拒绝 ───────────────────────────────────────

class TestValidation:


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

    @case_matrix("mutate", [
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

    def test_reload_observes_external_rule_change(self, tmp_path):
        path = write_rule(tmp_path, minimal_rule(name="旧名称"))
        mgr = TuningRuleManager(rules_dir=tmp_path)
        assert mgr.get_all_rule_keys_and_names() == [("t1", "旧名称")]

        path.write_text(
            yaml.dump(minimal_rule(name="新的名称"), allow_unicode=True),
            encoding="utf-8",
        )
        mgr.reload()

        assert mgr.get_all_rule_keys_and_names() == [("t1", "新的名称")]


    def test_create_duplicate_key_rejected(self, tmp_path):
        write_rule(tmp_path, minimal_rule())
        mgr = TuningRuleManager(rules_dir=tmp_path)
        with pytest.raises(RuleValidationError):
            mgr.create_rule("t1", "重复")


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


# ─── 规则可引用词表守护 ────────────────────────────────────────

class TestStandardAffixNames:

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


# ─── 基础配置 tune_config ─────────────────────────

def _valid_config() -> dict:
    thresholds = {p: ["gold"] for p in QUALITY_PARTS}
    thresholds["冠胄"] = ["gold", "purple"]
    return {
        "quality_thresholds": thresholds,
        "switches": {"keep_pvp": {"name": "保留PVP装备"}},
    }


class TestTuneConfig:


    @case_matrix("mutate", [
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

    @case_matrix("legacy", ["min_level", "materials", "behavior"])
    def test_legacy_sections_rejected(self, legacy):
        # 0.1 预览版硬拒绝：旧段已迁移至 base_groups/*.yaml
        data = _valid_config()
        data[legacy] = {}
        with pytest.raises(RuleValidationError, match="迁移"):
            parse_tune_config(data)

    @case_matrix("switches", [
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
        assert m.stone_min_count == 80
        assert m.stone_insufficient_action == "abort"
        assert m.food_rules == []

    def test_full_section_parsed(self):
        data = _valid_group()
        data["materials"] = {
            "stone_check": {"enabled": True, "min_count": 500,
                            "insufficient_action": "ask"},
            "food_rules": [
                {"parts": ["武器"], "max_quality": "gold_only",
                 "pct_op": "ge", "pct": 95, "ratings": ["top"],
                 "judge_scope": "incoming", "judge_rules": [],
                 "food": "彩狗粮", "on_insufficient": "skip"},
                {"max_quality": "purple_only", "pct_op": "ge", "pct": 0,
                 "food": "紫狗粮"},
                {"food": ""},                     # 终止规则：命中即不添加
            ],
        }
        m = parse_tuning_group(data).materials
        assert m.stone_check_enabled is True
        assert m.stone_min_count == 500
        assert m.stone_insufficient_action == "ask"
        assert m.food_rules == [
            FoodRule(parts=["武器"], max_quality="gold_only", pct=95,
                     ratings=["top"],
                     food="彩狗粮", on_insufficient="skip"),
            FoodRule(max_quality="purple_only", pct=0,
                     food="紫狗粮"),
            FoodRule(),
        ]


    def test_legacy_food_strategy_rejected(self):
        # 旧 food_strategy 段已废弃，出现即报错提示新写法
        data = _valid_group()
        data["materials"] = {"food_strategy": {"high_pct": 90}}
        with pytest.raises(RuleValidationError, match="已废弃"):
            parse_tuning_group(data)


    @case_matrix("materials", [
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
        {"food_rules": [{"min_expect": "top"}]},        # 旧字段已废弃
        {"food_rules": [{"min_quality": "gold"}]},      # 旧字段已废弃
        {"food_rules": [{"max_quality": "green"}]},     # 品阶非法
        {"food_rules": [{"pct_op": "le"}]},             # 狗粮仅允许 >=
        {"food_rules": [{"first_affix_only": True}]},    # 扫描专用字段
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
        # scan/tune 段缺省 → 空规则表，门槛与重置设置取默认值
        g = parse_tuning_group(_valid_group())
        assert g.scan.rules == []
        assert g.scan.entry_min_rating == "excellent"
        assert g.tune.rules == []
        assert g.tune.max_resets == MAX_TUNE_RESETS
        assert g.tune.reset_exhausted_action == "skip"

    def test_full_section_parsed(self):
        data = _valid_group()
        data["scan"] = {
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
        assert g.scan.entry_min_rating == "top"
        assert g.scan.rules == [BehaviorRule(
            parts=["武器"], max_quality="blue", ratings=["junk"],
            judge_scope="custom",
            judge_rules=["huiyi_general", "heal_pure"],
            first_affix_only=True, action="recycle")]
        # 判定语义逐规则声明，缺省 incoming
        assert [r.judge_scope for r in g.tune.rules] == [
            "all", "incoming", "incoming", "incoming"]
        assert [r.action for r in g.tune.rules] == [
            "recycle", "reset", "skip", "continue"]
        assert [r.enabled for r in g.tune.rules] == [False, True, True, True]
        assert g.tune.max_resets == 2
        assert g.tune.reset_exhausted_action == "recycle"

    def test_stage_missing_defaults(self):
        # 只声明 tune → scan 取默认门槛
        data = _valid_group()
        data["tune"] = {}
        g = parse_tuning_group(data)
        assert g.tune.rules == []
        assert g.scan.entry_min_rating == "excellent"

    def test_legacy_recycle_rejected(self):
        # 旧 recycle 段已废弃，出现即报错提示新写法
        data = _valid_config()
        data["recycle"] = {"scan": {"enabled": True}}
        with pytest.raises(RuleValidationError, match="behavior"):
            parse_tune_config(data)

    @case_matrix("scan", [
        ["not", "a", "dict"],                            # 段须为 dict
        {"enabled": True},                               # 阶段级开关已废弃
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

    @case_matrix("tune", [
        ["not", "a", "dict"],                            # 段须为 dict
        {"enabled": True},                               # 阶段级开关已废弃
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
        # 处置表自上而下首条命中；无命中 → skip 跳过
        junk = _rating("junk")
        data = _valid_group()
        data["scan"] = {"rules": [
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
    def test_disabled_behavior_rule_is_skipped(self):
        scan = ScanBehavior(rules=[
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
        data["scan"] = {"rules": [
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
        data["scan"] = {"rules": [
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
        data["scan"] = {"rules": [
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
        data["scan"] = {"rules": [
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
        # 无命中默认：未满 = 继续调律；词条满 = 结束并保留；
        # full=True 时 continue 规则转为 lock，并终止后续规则判定
        junk, normal, top = (_rating("junk"), _rating("normal"),
                             _rating("top"))
        data = _valid_group()
        data["tune"] = {"rules": [
            {"max_rating": "junk", "action": "recycle"},
            {"max_rating": "normal", "action": "continue"},
        ]}
        tune = parse_tuning_group(data).tune
        # 首条命中 → 回收（满/未满一致）
        assert tune.decide("武器", "gold", 95, junk, False)[0] == "recycle"
        assert tune.decide("武器", "gold", 95, junk, True)[0] == "recycle"
        # 次条 continue：未满命中生效；词条满后只有传入规则最终评级
        # 达到优秀才转锁定，普通档只结束保留。
        assert tune.decide("武器", "gold", 95, normal,
                           False)[0] == "continue"
        assert tune.decide(
            "武器", "gold", 95, normal, True,
            final_rating="normal")[0] == "skip"
        assert tune.decide(
            "武器", "gold", 95, normal, True,
            final_rating="excellent")[0] == "lock"
        guarded = TuneBehavior(rules=[
            BehaviorRule(ratings=["top"], action="continue"),
            BehaviorRule(action="recycle"),
        ])
        assert guarded.decide(
            "武器", "gold", 95, top, True,
            final_rating="top")[0] == "lock"
        # 全部不命中 → 默认：未满继续、满后结束保留，不推导锁定资格
        assert tune.decide("武器", "gold", 95, top, False)[0] == "continue"
        assert tune.decide("武器", "gold", 95, top, True)[0] == "skip"
        # 空规则表 → 同默认
        assert TuneBehavior().decide(
            "武器", "gold", 95, junk, False)[0] == "continue"
        assert TuneBehavior().decide(
            "武器", "gold", 95, junk, True)[0] == "skip"
        assert TuneBehavior(lock_qualified=False).decide(
            "武器", "gold", 95, junk, True)[0] == "skip"


class TestDecideFood:
    """统一条件匹配 + 持有量判定 + 三种不足策略。"""

    STOCKS = {"彩狗粮": 5, "金狗粮": 3, "紫狗粮": 0}

    @staticmethod
    def _decide(materials, pct, rating, quality, stocks,
                part="武器", affix_names=None):
        return materials.decide_food(
            part, quality, pct, _rating(rating), stocks,
            affix_names or ["最大外功攻击"])


    # 测试用狗粮规则（与 default.yaml 中的示例一致，但非“默认”）
    _RULES = [
        FoodRule(pct=98, ratings=["top"], food="彩狗粮"),
        FoodRule(pct=90, ratings=["top", "excellent"], food="金狗粮"),
    ]


    def test_rating_provider_uses_rule_scope_and_keys(self):
        calls = []

        def rating_of(scope, keys, first_affix_only=False):
            calls.append((scope, keys, first_affix_only))
            return "excellent"

        m = MaterialSettings(food_rules=[FoodRule(
            judge_scope="custom", judge_rules=["heal_pure"],
            ratings=["excellent"], food="金狗粮")])
        d = m.decide_food(
            "胸甲", "gold", 90, rating_of, {"金狗粮": 1}, ["会心率"])
        assert (d.action, d.food) == ("feed", "金狗粮")
        assert calls == [("custom", ["heal_pure"], False)]

    def test_affix_scope_matches_names_without_rating(self):
        def unexpected_rating(*_args):
            pytest.fail("自选词条语义不应执行评级")

        m = MaterialSettings(food_rules=[FoodRule(
            judge_scope="affix", ratings=["会心率"], food="紫狗粮")])
        d = m.decide_food(
            "冠胄", "purple", 90, unexpected_rating,
            {"紫狗粮": 1}, ["会心率", "气血最大值"])
        assert (d.action, d.food) == ("feed", "紫狗粮")


    def test_cap_pct_none_fails_positive_pct(self):
        # pct>0 时 cap_pct 识别失败视为不达标
        m = MaterialSettings(food_rules=self._RULES)
        d = self._decide(m, None, "top", "gold", self.STOCKS)
        assert d.action == "none"


    def test_exact_quality_and_part_conditions(self):
        m = MaterialSettings(food_rules=[
            FoodRule(parts=["胸甲"], max_quality="gold_only",
                     food="金狗粮"),
            FoodRule(parts=["武器"], max_quality="purple_only",
                     food="紫狗粮"),
        ])
        stocks = {"金狗粮": 9, "紫狗粮": 9}
        assert self._decide(
            m, 50, "normal", "gold", stocks, part="武器").action == "none"
        d = self._decide(
            m, 50, "normal", "purple", stocks, part="武器")
        assert (d.action, d.food) == ("feed", "紫狗粮")

    def test_insufficient_next_falls_through(self):
        m = MaterialSettings(food_rules=[
            FoodRule(food="彩狗粮", on_insufficient="next"),
            FoodRule(food="金狗粮"),
        ])
        d = self._decide(m, 50, "normal", "blue", {"金狗粮": 2})
        assert (d.action, d.food) == ("feed", "金狗粮")
        assert "顺延" in d.reason

    def test_insufficient_continue_stops_rules_and_tunes_without_food(self):
        m = MaterialSettings(food_rules=[
            FoodRule(food="彩狗粮", on_insufficient="continue"),
            FoodRule(food="金狗粮"),
        ])
        d = self._decide(m, 50, "normal", "blue", {"金狗粮": 2})
        assert (d.action, d.food) == ("none", "")
        assert "继续调律" in d.reason

    def test_insufficient_skip_aborts_equipment(self):
        m = MaterialSettings(food_rules=[
            FoodRule(food="彩狗粮", on_insufficient="skip"),
            FoodRule(food="金狗粮"),
        ])
        d = self._decide(m, 50, "normal", "blue", {"金狗粮": 2})
        assert d.action == "skip"
        assert "跳过" in d.reason


    def test_builtin_colorful_shortage_downgrades_to_gold(self):
        m = get_tuning_group("default").materials
        d = self._decide(
            m, 99, "top", "gold", {"彩狗粮": 0, "金狗粮": 2})
        assert (d.action, d.food) == ("feed", "金狗粮")
        assert "彩狗粮" in d.reason and "顺延" in d.reason


# ─── 规则级品阶门槛覆盖 ─────────────────

class TestRuleQualityThresholds:
    def test_optional_and_subset_allowed(self):
        rule = parse_tuning_rule(minimal_rule())
        assert rule.quality_thresholds == {}
        rule = parse_tuning_rule(minimal_rule(
            quality_thresholds={"佩": ["gold", "purple"]}))
        assert rule.quality_thresholds == {"佩": ["gold", "purple"]}

    @case_matrix("thresholds", [
        {"default": ["gold"]},   # 未知部位
        {"佩": ["legendary"]},   # 非法品阶
    ])
    def test_quality_threshold_rejected(self, thresholds):
        with pytest.raises(RuleValidationError):
            parse_tuning_rule(minimal_rule(quality_thresholds=thresholds))


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

    @case_matrix("key,name,match", [
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
