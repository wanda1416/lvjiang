"""会意通用规则（huiyi_general）判定测试

覆盖各部位四档条件（junk → normal → excellent → top 档序）、
鸣金属性分部位（武器写 最大无相攻击 / 非武器写 最大鸣金攻击）、
开关 keep_pvp（条件组 when 语义）、品阶/首词条筛选、规则注册与
调律潜力判定。
会心/治疗规则测试见 test_huixin_judge.py / test_heal_judge.py。
"""

import pytest

from lvjiang.apps.yysls.equip_parser.models import Affix, EquipmentData
from lvjiang.apps.yysls.evaluator import (
    Rating,
    get_rule_names,
    get_tuning_judge,
    get_tuning_rules,
    is_rule_implemented,
    judge_tuning_worthiness,
)


def make_equip(equip_type: str, affix_names: list[str],
               quality: str | None = "gold") -> EquipmentData:
    """构造测试装备（affix_names 第 1 条为首词条）"""
    return EquipmentData(
        type=equip_type,
        name="测试装备",
        level=110,
        quality=quality,
        affixes=[Affix(name=n, value=1.0) for n in affix_names],
    )


@pytest.fixture
def judge():
    return get_tuning_judge("huiyi_general")


@pytest.fixture
def judge_pvp():
    return get_tuning_judge("huiyi_general",
                            {"switches": {"keep_pvp": True}})


# ─── 主武器（剑，会意规则需要 剑武学增伤） ─────────────────

class TestJian:
    def test_top(self, judge):
        e = make_equip("剑", ["最大外功攻击", "剑武学增伤", "最大外功攻击", "劲", "势"])
        assert judge.judge(e).rating == Rating.TOP

    def test_excellent_huiyi(self, judge):
        # 会意率 破坏顶级排除条件 → 优秀
        e = make_equip("剑", ["最大外功攻击", "剑武学增伤", "最大外功攻击", "势", "会意率"])
        assert judge.judge(e).rating == Rating.EXCELLENT

    def test_normal_missing_jin_shi(self, judge):
        # 劲/势 全缺不算缺陷（可调出）→ 优秀
        e = make_equip("剑", ["最大外功攻击", "剑武学增伤", "最大外功攻击", "会意率", "最大无相攻击"])
        assert judge.judge(e).rating == Rating.EXCELLENT

    def test_normal_defect_huixin(self, judge):
        # 会心率 为会意流缺陷词条 → 一般
        e = make_equip("剑", ["最大外功攻击", "剑武学增伤", "最大外功攻击", "势", "会心率"])
        assert judge.judge(e).rating == Rating.NORMAL

    def test_junk_missing_damage(self, judge):
        e = make_equip("剑", ["最大外功攻击", "最大外功攻击", "劲", "势", "会意率"])
        r = judge.judge(e)
        assert r.rating == Rating.JUNK
        assert any("增伤" in s for s in r.reasons)

    def test_junk_wrong_wuxue(self, judge):
        # 枪武学增伤 非本次增伤且池外 → 垃圾
        e = make_equip("剑", ["最大外功攻击", "枪武学增伤", "最大外功攻击", "劲", "势"])
        assert judge.judge(e).rating == Rating.JUNK

    def test_junk_before_normal(self, judge):
        # 会心+精准 双出同时命中 垃圾(≥2) 与 一般(≥1) 条件 → 垃圾优先
        e = make_equip("剑", ["最大外功攻击", "剑武学增伤", "最大外功攻击", "会心率", "精准率"])
        assert judge.judge(e).rating == Rating.JUNK


# ─── 副武器（枪，不需增伤） ────────────────────────────────

class TestQiang:
    def test_top_jin_and_shi(self, judge):
        e = make_equip("枪", ["最大外功攻击", "最大外功攻击", "劲", "势", "最大无相攻击"])
        assert judge.judge(e).rating == Rating.TOP

    def test_excellent_double_jin(self, judge):
        # 双劲无势 → 顶级 contains_all[劲,势] 不成立 → 优秀
        e = make_equip("枪", ["最大外功攻击", "最大外功攻击", "劲", "劲", "会意率"])
        assert judge.judge(e).rating == Rating.EXCELLENT

    def test_normal_missing_jin_shi(self, judge):
        # 缺劲/势/大外不算缺陷 → 优秀
        e = make_equip("枪", ["最大外功攻击", "最大外功攻击", "会意率", "最大无相攻击", "最大无相攻击"])
        assert judge.judge(e).rating == Rating.EXCELLENT

    def test_normal_defect_huixin(self, judge):
        # 精准率 为会意流缺陷词条 → 一般
        e = make_equip("枪", ["最大外功攻击", "最大外功攻击", "劲", "势", "精准率"])
        assert judge.judge(e).rating == Rating.NORMAL

    def test_junk_qiang_wuxue(self, judge):
        e = make_equip("枪", ["最大外功攻击", "枪武学增伤", "最大外功攻击", "劲", "势"])
        assert judge.judge(e).rating == Rating.JUNK


# ─── 环、佩（归并部位） ────────────────────────────────────

class TestJewelry:
    def test_top(self, judge):
        e = make_equip("环", ["最大外功攻击", "最大外功攻击", "全武学增效", "劲", "势"])
        assert judge.judge(e).rating == Rating.TOP

    def test_pei_alias_excellent(self, judge):
        # 佩 归并 环；大鸣金入池但只有劲没有势 → 优秀
        e = make_equip("佩", ["最大外功攻击", "最大外功攻击", "全武学增效", "劲", "最大鸣金攻击"])
        assert judge.judge(e).rating == Rating.EXCELLENT

    def test_excellent_double_jin(self, judge):
        e = make_equip("环", ["最大外功攻击", "最大外功攻击", "全武学增效", "劲", "劲"])
        assert judge.judge(e).rating == Rating.EXCELLENT

    def test_junk_missing_quan_wuxue(self, judge):
        e = make_equip("环", ["最大外功攻击", "最大外功攻击", "劲", "势", "劲"])
        assert judge.judge(e).rating == Rating.JUNK


# ─── 冠胄（首词条 会意率） ─────────────────────────────────

class TestHelm:
    def test_top(self, judge):
        e = make_equip("冠胄", ["会意率", "最大外功攻击", "劲", "势", "会意率"],
                       quality="purple")
        assert judge.judge(e).rating == Rating.TOP

    def test_excellent_mingjin(self, judge):
        # 大鸣金 破坏顶级排除条件 → 优秀
        e = make_equip("冠胄", ["会意率", "最大外功攻击", "劲", "势", "最大鸣金攻击"],
                       quality="purple")
        assert judge.judge(e).rating == Rating.EXCELLENT

    def test_normal_single_huixin(self, judge):
        e = make_equip("冠胄", ["会意率", "会心率", "最大外功攻击", "劲", "势"],
                       quality="purple")
        assert judge.judge(e).rating == Rating.NORMAL

    def test_junk_double_huixin(self, judge):
        e = make_equip("冠胄", ["会意率", "会心率", "精准率", "最大外功攻击", "劲"],
                       quality="purple")
        assert judge.judge(e).rating == Rating.JUNK


# ─── 胫甲（首词条 劲，对首领增要求挂 keep_pvp 开关） ──────

class TestLeg:
    def test_top(self, judge):
        e = make_equip("胫甲", ["劲", "对首领单位增伤", "最大外功攻击", "劲", "势"],
                       quality="purple")
        assert judge.judge(e).rating == Rating.TOP

    def test_junk_missing_boss_damage(self, judge):
        e = make_equip("胫甲", ["劲", "最大外功攻击", "最大鸣金攻击", "劲", "势"],
                       quality="purple")
        assert judge.judge(e).rating == Rating.JUNK


# ─── 开关 keep_pvp（条件组 when） ────────────────────────────────────

class TestKeepPvp:
    def test_leg_pvp_off_junk(self, judge):
        # 未开启：缺对首领增 + 出现对玩家增 → 垃圾（when off 组命中）
        e = make_equip("胫甲", ["劲", "对玩家单位增效", "最大外功攻击", "劲", "势"],
                       quality="purple")
        assert judge.judge(e).rating == Rating.JUNK

    def test_leg_pvp_on_top(self, judge_pvp):
        # 开启：off 垃圾组失效，对玩家增视作有效增伤 → 顶级
        e = make_equip("胫甲", ["劲", "对玩家单位增效", "最大外功攻击", "劲", "势"],
                       quality="purple")
        assert judge_pvp.judge(e).rating == Rating.TOP

    def test_leg_pvp_on_both_missing_junk(self, judge_pvp):
        # 开启：对首领增/对玩家增 两者皆缺 → 垃圾（when on 组命中）
        e = make_equip("胫甲", ["劲", "最大外功攻击", "最大鸣金攻击", "劲", "势"],
                       quality="purple")
        assert judge_pvp.judge(e).rating == Rating.JUNK

    def test_helm_pvp_off_junk(self, judge):
        e = make_equip("冠胄", ["会意率", "单体类奇术增伤", "最大外功攻击", "劲", "势"],
                       quality="purple")
        assert judge.judge(e).rating == Rating.JUNK

    def test_helm_pvp_on_top(self, judge_pvp):
        # 开启：单体奇术垃圾组失效，池内词条不拖后腿 → 顶级
        e = make_equip("冠胄", ["会意率", "单体类奇术增伤", "最大外功攻击", "劲", "势"],
                       quality="purple")
        assert judge_pvp.judge(e).rating == Rating.TOP


# ─── 品阶与首词条筛选 ──────────────────────────────────────

class TestFilters:
    def test_purple_weapon_skipped(self, judge):
        e = make_equip("剑", ["最大外功攻击", "剑武学增伤", "最大外功攻击", "劲", "势"],
                       quality="purple")
        assert judge.judge(e).skipped

    def test_purple_armor_ok(self, judge):
        e = make_equip("冠胄", ["会意率", "最大外功攻击", "劲", "势", "会意率"],
                       quality="purple")
        assert not judge.judge(e).skipped

    def test_wrong_first_skipped(self, judge):
        e = make_equip("剑", ["劲", "剑武学增伤", "最大外功攻击", "劲", "势"])
        assert judge.judge(e).skipped

    def test_no_affix_skipped(self, judge):
        e = make_equip("剑", [])
        assert judge.judge(e).skipped


# ─── 规则注册 ──────────────────────────────────────────────

class TestRegistry:
    def test_get_rule_names_order(self):
        assert list(get_rule_names()) == [
            "huiyi_general", "huixin_small", "huixin_big",
            "heal_pure", "heal_fire",
        ]
        # 名称随规则文件 name 字段（可被用户改名），不硬编码
        assert get_rule_names()["huiyi_general"] == \
            get_tuning_rules()["huiyi_general"].name

    def test_all_implemented(self):
        for key in get_tuning_rules():
            assert is_rule_implemented(key)

    def test_unknown_rule_raises(self):
        with pytest.raises(ValueError):
            get_tuning_judge("no_such_rule")


# ─── 调律潜力（judge_tuning_worthiness 汇总） ──────────────

class TestWorthiness:
    def test_top_equipment_worth(self):
        e = make_equip("环", ["最大外功攻击", "全武学增效", "劲", "势"])
        worth, logs = judge_tuning_worthiness(
            e, rule_keys=["huiyi_general"])
        assert worth
        assert logs

    def test_junk_equipment_not_worth(self):
        # 双废词条不可救 → 不值得
        e = make_equip("环", ["最大外功攻击", "最大裂石攻击", "最大牵丝攻击", "会心率", "精准率"])
        worth, _ = judge_tuning_worthiness(
            e, rule_keys=["huiyi_general"])
        assert not worth

    def test_not_applicable_not_veto(self):
        # 扇 不在会意判定范围 → 无有效结论 → 不值得
        e = make_equip("扇", ["最大外功攻击", "劲", "势"])
        worth, logs = judge_tuning_worthiness(
            e, rule_keys=["huiyi_general"])
        assert not worth
        assert any("不适用" in s for s in logs)


# ─── 潜力模拟：转律词条域 / 可转律开关 / 转律标注 ──────────

class TestTransmuteSimulation:
    def test_out_of_library_affix_not_obtainable(self, judge):
        # 对首领单位增伤 不在转律词条库 → 转律补不出，垃圾封顶不可解除
        e = make_equip("腕甲",
                       ["劲", "最大外功攻击", "对玩家单位增效", "劲", "势"])
        res = judge.check_tuning_worthiness(e)
        text = "；".join(res.reasons)
        assert res.rating == Rating.JUNK
        assert "对首领单位增伤" in text
        assert "转律为" not in text

    def test_reason_tagged_with_transmute(self, judge):
        # 转律变体胜出：标注「{转出} 转律为 {转入}」
        e = make_equip("剑", ["最大外功攻击", "最大外功攻击", "最小外功攻击"])
        res = judge.check_tuning_worthiness(e)
        assert res.rating == Rating.TOP
        assert "最小外功攻击 转律为" in "；".join(res.reasons)

    def test_baseline_win_no_tag(self, judge_pvp):
        # 基线（未转律）命中：不加转律标注
        e = make_equip("腕甲",
                       ["劲", "最大外功攻击", "对玩家单位增效", "劲", "势"])
        res = judge_pvp.check_tuning_worthiness(e)
        assert res.rating == Rating.TOP
        assert "转律为" not in "；".join(res.reasons)

    def test_can_transmute_off(self):
        # 可转律关闭：放弃转律模拟，仅按空槽评估
        judge = get_tuning_judge("huiyi_general", {"can_transmute": False})
        e = make_equip("剑", ["最大外功攻击", "最大外功攻击", "最小外功攻击"])
        res = judge.check_tuning_worthiness(e)
        assert res.rating == Rating.JUNK
        assert "垃圾词条" in "；".join(res.reasons)


# ─── 填充式潜力：价值序填空槽 + 部位过滤 + 缺增伤占槽 ──────

class TestFillSimulation:
    def test_qiang_free_slots_fill_huixin_top(self, judge):
        # 副武器枪回归：空槽按价值序填 势/会意率 → 顶级双条件
        # contains_all[劲,势,最大外功攻击] + count_min[会意率,最大无相]≥1 命中
        e = make_equip("枪", ["最大外功攻击", "劲"])
        res = judge.check_tuning_worthiness(e)
        assert res.rating == Rating.TOP

    def test_fill_part_filter_quan_wuxue(self, judge):
        # 环空槽可填 全武学增效（环/佩专属，武器专属的最大无相被过滤）
        # → 逃离环垃圾条件并命中顶级
        e = make_equip("环", ["最大外功攻击"])
        res = judge.check_tuning_worthiness(e)
        assert res.rating == Rating.TOP
        assert "全武学增效" in "；".join(res.reasons)

    def test_weapon_free_slot_fills_damage_top(self, judge):
        # 主武器剑缺增伤：第一个空槽补 剑武学增伤 → 顶级
        e = make_equip("剑", ["最大外功攻击", "最大外功攻击", "劲", "势"])
        res = judge.check_tuning_worthiness(e)
        assert res.rating == Rating.TOP
        assert "剑武学增伤" in "；".join(res.reasons)

    def test_weapon_full_missing_damage_junk(self, judge):
        # 词条已满且缺增伤：无空槽补增伤 → 垃圾
        e = make_equip("剑",
                       ["最大外功攻击", "最大外功攻击", "劲", "势", "会意率"])
        res = judge.check_tuning_worthiness(e)
        assert res.rating == Rating.JUNK
        assert any("增伤" in s for s in res.reasons)

    def test_transmute_gain_skips_existing(self, judge):
        # 转出池外 最小外功攻击、转入跳过已存在（势）取次高 劲 → 顶级
        e = make_equip("剑", ["最大外功攻击", "势", "最小外功攻击"])
        res = judge.check_tuning_worthiness(e)
        assert res.rating == Rating.TOP
        assert "最小外功攻击 转律为 劲" in "；".join(res.reasons)


# ─── 部位级默认判定兜底 ────────────────────────────

class TestPatternDefaultRating:
    @staticmethod
    def _make_judge(pattern_default: str | None):
        from lvjiang.apps.yysls.evaluator.judge import GenericTuningJudge
        from lvjiang.apps.yysls.evaluator.tuning_rules import parse_tuning_rule
        data = {
            "key": "t1",
            "name": "测试规则",
            "playstyles": {"测试": {
                "main": {"weapon": "剑", "damage": "剑武学增伤"},
                "sub": {"weapon": "枪", "damage": None},
                "attr": "通用"}},
            "affix_pool": ["最大外功攻击", "劲"],
            "patterns": {"环": {
                "first": ["最大外功攻击"],
                # 带 劲 时垃圾/顶级均不命中 → 落入默认判定
                "junk_conditions": [
                    {"count_max": {"symbols": ["劲"], "max": 0}}],
                "top_conditions": [
                    [{"contains_all": ["劲"]},
                     {"count_max": {"symbols": ["最大外功攻击"],
                                    "max": 0}}],
                ],
            }},
        }
        if pattern_default:
            data["patterns"]["环"]["default_rating"] = pattern_default
        return GenericTuningJudge(parse_tuning_rule(data))

    def test_follow_rule_level_default(self):
        # 部位未设置 → 跟随规则级默认（excellent）
        e = make_equip("环", ["最大外功攻击", "劲", "最大外功攻击",
                             "劲", "劲"])
        res = self._make_judge(None).judge(e)
        assert res.rating == Rating.EXCELLENT

    def test_pattern_level_overrides(self):
        # 部位级 default_rating=junk 覆盖规则级
        e = make_equip("环", ["最大外功攻击", "劲", "最大外功攻击",
                             "劲", "劲"])
        res = self._make_judge("junk").judge(e)
        assert res.rating == Rating.JUNK


# ─── 通用判定（规则级四档条件，对所有部位生效） ────

class TestCommonConditionsJudge:
    @staticmethod
    def _make_judge(common: dict | None, config: dict | None = None):
        from lvjiang.apps.yysls.evaluator.judge import GenericTuningJudge
        from lvjiang.apps.yysls.evaluator.tuning_rules import parse_tuning_rule
        data = {
            "key": "t1",
            "name": "测试规则",
            "playstyles": {"测试": {
                "main": {"weapon": "剑", "damage": "剑武学增伤"},
                "sub": {"weapon": "枪", "damage": None},
                "attr": "通用"}},
            "affix_pool": ["最大外功攻击", "劲", "势"],
            "patterns": {"环": {
                "first": ["最大外功攻击"],
                "top_conditions": [{"contains_all": ["劲"]}],
            }},
        }
        if common:
            data["common_conditions"] = common
        return GenericTuningJudge(parse_tuning_rule(data), config)

    def test_common_junk_applies_to_part(self):
        # 部位未定义垃圾档：通用判定的垃圾条件仍逐档并入生效
        judge = self._make_judge(
            {"junk_conditions": [{"contains_all": ["势"]}]})
        e = make_equip("环", ["最大外功攻击", "势", "劲"])
        res = judge.judge(e)
        assert res.rating == Rating.JUNK
        assert "命中垃圾条件" in "；".join(res.reasons)
        # 未触发通用条件时照常走部位条件（命中顶级）
        e2 = make_equip("环", ["最大外功攻击", "劲"])
        assert judge.judge(e2).rating == Rating.TOP

    def test_without_common_part_tier_only(self):
        # 无通用判定：同装备仅按部位条件判定
        judge = self._make_judge(None)
        e = make_equip("环", ["最大外功攻击", "势", "劲"])
        assert judge.judge(e).rating == Rating.TOP

    def test_common_group_respects_when(self):
        # 通用条件组同样支持开关前提 when
        common = {"junk_conditions": [
            {"when": {"keep_pvp": True},
             "all": [{"contains_all": ["势"]}]}]}
        e = make_equip("环", ["最大外功攻击", "势", "劲"])
        # 开关关闭（缺省 False）：条件组不参与 → 顶级
        assert self._make_judge(common).judge(e).rating == Rating.TOP
        # 开关打开：通用垃圾条件生效
        judge_on = self._make_judge(
            common, {"switches": {"keep_pvp": True}})
        assert judge_on.judge(e).rating == Rating.JUNK

    def test_common_in_potential_eval(self):
        # 调律潜力判定同样并入通用条件（填充后复用完整定级）
        judge = self._make_judge(
            {"junk_conditions": [{"contains_all": ["势"]}]},
            {"can_transmute": False})
        e = make_equip("环", ["最大外功攻击", "势", "劲", "劲", "劲"])
        assert judge.check_tuning_worthiness(e).rating == Rating.JUNK
