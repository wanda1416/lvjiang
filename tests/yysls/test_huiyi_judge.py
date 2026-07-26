"""会意流派-通用 判定器测试（通用规则引擎）

覆盖 01-会意流派调律说明.md 第九节全部判定例子，
以及品阶/首词条筛选、keep_pvp、流派注册等边界场景。
会心/治疗流派测试见 test_huixin_judge.py / test_heal_judge.py。
"""

import pytest

from src.apps.yysls.equip_parser.models import Affix, EquipmentData
from src.apps.yysls.evaluator import (
    Rating, get_school_judge, get_school_rules, get_schools,
    is_school_implemented, judge_tuning_worthiness,
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
    return get_school_judge("huiyi_general")


@pytest.fixture
def judge_pvp():
    return get_school_judge("huiyi_general", {"keep_pvp": True})


# ─── 第九节判定例子：主武器（剑） ──────────────────────────

class TestJian:
    def test_top(self, judge):
        e = make_equip("剑", ["最大外功攻击", "剑武学增伤", "最大外功攻击", "劲", "势"])
        assert judge.judge(e).rating == Rating.TOP

    def test_excellent_huiyi(self, judge):
        e = make_equip("剑", ["最大外功攻击", "剑武学增伤", "最大外功攻击", "势", "会意率"])
        assert judge.judge(e).rating == Rating.EXCELLENT

    def test_usable_missing_jin_shi(self, judge):
        e = make_equip("剑", ["最大外功攻击", "剑武学增伤", "最大外功攻击", "会意率", "最大无相攻击"])
        assert judge.judge(e).rating == Rating.USABLE

    def test_junk_missing_damage(self, judge):
        e = make_equip("剑", ["最大外功攻击", "最大外功攻击", "劲", "势", "会意率"])
        r = judge.judge(e)
        assert r.rating == Rating.JUNK
        assert any("增伤" in s for s in r.reasons)

    def test_junk_wrong_wuxue(self, judge):
        e = make_equip("剑", ["最大外功攻击", "枪武学增伤", "最大外功攻击", "劲", "势"])
        assert judge.judge(e).rating == Rating.JUNK


# ─── 第九节判定例子：副武器（枪） ──────────────────────────

class TestQiang:
    def test_top_jin_and_shi(self, judge):
        e = make_equip("枪", ["最大外功攻击", "最大外功攻击", "劲", "势", "最大无相攻击"])
        assert judge.judge(e).rating == Rating.TOP

    def test_excellent_double_jin(self, judge):
        e = make_equip("枪", ["最大外功攻击", "最大外功攻击", "劲", "劲", "会意率"])
        assert judge.judge(e).rating == Rating.EXCELLENT

    def test_usable_missing_jin_shi(self, judge):
        e = make_equip("枪", ["最大外功攻击", "最大外功攻击", "会意率", "最大无相攻击", "最大无相攻击"])
        assert judge.judge(e).rating == Rating.USABLE

    def test_junk_mingjin_on_weapon(self, judge):
        # 武器上出现 最大鸣金攻击，属错位属攻 → 垃圾
        e = make_equip("枪", ["最大外功攻击", "最大外功攻击", "劲", "势", "最大鸣金攻击"])
        assert judge.judge(e).rating == Rating.JUNK

    def test_junk_qiang_wuxue(self, judge):
        e = make_equip("枪", ["最大外功攻击", "枪武学增伤", "最大外功攻击", "劲", "势"])
        assert judge.judge(e).rating == Rating.JUNK


# ─── 第九节判定例子：环、佩 ────────────────────────────────

class TestJewelry:
    def test_top(self, judge):
        e = make_equip("环", ["最大外功攻击", "最大外功攻击", "全武学增效", "劲", "势"])
        assert judge.judge(e).rating == Rating.TOP

    def test_excellent_dawuxiang(self, judge):
        # 大无相在非武器部位即 最大鸣金攻击（大本属）；只有劲没有势 → 优秀
        e = make_equip("佩", ["最大外功攻击", "最大外功攻击", "全武学增效", "劲", "最大鸣金攻击"])
        assert judge.judge(e).rating == Rating.EXCELLENT

    def test_excellent_double_jin(self, judge):
        # 池内重复取劲合法，但未同时出现 劲+势 → 优秀
        e = make_equip("环", ["最大外功攻击", "最大外功攻击", "全武学增效", "劲", "劲"])
        assert judge.judge(e).rating == Rating.EXCELLENT

    def test_junk_wuxiang_on_jewelry(self, judge):
        # 非武器上出现 最大无相攻击，属错位属攻 → 垃圾
        e = make_equip("环", ["最大外功攻击", "最大外功攻击", "全武学增效", "劲", "最大无相攻击"])
        assert judge.judge(e).rating == Rating.JUNK

    def test_junk_missing_quan_wuxue(self, judge):
        e = make_equip("环", ["最大外功攻击", "最大外功攻击", "劲", "势", "劲"])
        assert judge.judge(e).rating == Rating.JUNK


# ─── 第九节判定例子：冠胄、胸甲 ────────────────────────────

class TestHelmChest:
    def test_top_double_huiyi(self, judge):
        e = make_equip("冠胄", ["会意率", "最大外功攻击", "劲", "势", "会意率"], quality="purple")
        assert judge.judge(e).rating == Rating.TOP

    def test_excellent_dawuxiang(self, judge):
        e = make_equip("胸甲", ["会意率", "最大外功攻击", "势", "最大鸣金攻击", "劲"], quality="purple")
        assert judge.judge(e).rating == Rating.EXCELLENT

    def test_usable_one_huixin(self, judge):
        e = make_equip("冠胄", ["会意率", "最大外功攻击", "劲", "会心率", "势"], quality="purple")
        assert judge.judge(e).rating == Rating.USABLE

    def test_junk_two_rate_affixes(self, judge):
        e = make_equip("冠胄", ["会意率", "最大外功攻击", "会心率", "精准率", "劲"], quality="purple")
        r = judge.judge(e)
        assert r.rating == Rating.JUNK
        assert any("会心率/精准率" in s for s in r.reasons)


# ─── 第九节判定例子：胫甲、腕甲 ────────────────────────────

class TestLegWrist:
    def test_top(self, judge):
        e = make_equip("胫甲", ["劲", "对首领单位增伤", "最大外功攻击", "劲", "势"])
        assert judge.judge(e).rating == Rating.TOP

    def test_excellent_no_shi(self, judge):
        e = make_equip("腕甲", ["劲", "对首领单位增伤", "最大外功攻击", "劲", "会意率"])
        assert judge.judge(e).rating == Rating.EXCELLENT

    def test_top_pvp_substitute(self, judge_pvp):
        # 对玩家增效视作有效（PVP/百业战保留）
        e = make_equip("胫甲", ["劲", "对玩家单位增效", "最大外功攻击", "劲", "势"])
        r = judge_pvp.judge(e)
        assert r.rating == Rating.TOP
        assert r.is_pvp

    def test_excellent_missing_jin_shi(self, judge):
        # 可选槽=会意+大无相：命中模式但非首词条未同时出现 劲+势 → 优秀
        e = make_equip("胫甲", ["劲", "对首领单位增伤", "最大外功攻击", "会意率", "最大鸣金攻击"])
        assert judge.judge(e).rating == Rating.EXCELLENT

    def test_junk_missing_boss_damage(self, judge):
        e = make_equip("胫甲", ["劲", "最大外功攻击", "劲", "势", "会意率"])
        assert judge.judge(e).rating == Rating.JUNK


# ─── 品阶与首词条筛选 ──────────────────────────────────────

class TestScreening:
    def test_weapon_purple_skipped(self, judge):
        e = make_equip("剑", ["最大外功攻击", "剑武学增伤", "最大外功攻击", "劲", "势"],
                       quality="purple")
        assert judge.judge(e).skipped

    def test_armor_purple_not_skipped(self, judge):
        e = make_equip("冠胄", ["会意率", "最大外功攻击", "劲", "势", "会意率"], quality="purple")
        assert not judge.judge(e).skipped

    def test_quality_none_proceeds(self, judge):
        # 品阶未识别时跳过品阶筛选，继续判定
        e = make_equip("剑", ["最大外功攻击", "剑武学增伤", "最大外功攻击", "劲", "势"],
                       quality=None)
        r = judge.judge(e)
        assert not r.skipped
        assert r.rating == Rating.TOP

    def test_wrong_first_affix_skipped(self, judge):
        e = make_equip("环", ["劲", "最大外功攻击", "全武学增效", "劲", "势"])
        assert judge.judge(e).skipped

    def test_weapon_shi_first_skipped(self, judge):
        # 会意武器首词条只认大外（势次选已删除）：势首 → 跳过
        e = make_equip("剑", ["势", "剑武学增伤", "最大外功攻击", "劲", "势"])
        r = judge.judge(e)
        assert r.skipped and not r.not_applicable

    def test_undefined_type_skipped(self, judge):
        e = make_equip("陌刀", ["最大外功攻击", "最大外功攻击", "劲", "势", "会意率"])
        assert judge.judge(e).skipped


# ─── keep_pvp 差异 ─────────────────────────────────────────

class TestKeepPvp:
    def test_helm_qishu_kept(self, judge_pvp):
        e = make_equip("冠胄", ["会意率", "最大外功攻击", "劲", "势", "单体类奇术增伤"],
                       quality="purple")
        r = judge_pvp.judge(e)
        assert r.rating == Rating.USABLE
        assert r.is_pvp

    def test_helm_qishu_junk_without_pvp(self, judge):
        e = make_equip("冠胄", ["会意率", "最大外功攻击", "劲", "势", "单体类奇术增伤"],
                       quality="purple")
        assert judge.judge(e).rating == Rating.JUNK

    def test_leg_player_effect_junk_without_pvp(self, judge):
        e = make_equip("胫甲", ["劲", "对玩家单位增效", "最大外功攻击", "劲", "势"])
        assert judge.judge(e).rating == Rating.JUNK


# ─── 流派注册与规则元数据 ──────────────────────────────────

class TestSchools:
    def test_schools_registry(self):
        assert list(get_schools()) == [
            "huiyi_general", "huixin_small", "huixin_big",
            "heal_pure", "heal_fire",
        ]

    def test_implemented_flags(self):
        for key in ("huiyi_general", "huixin_small", "huixin_big",
                    "heal_pure", "heal_fire"):
            assert is_school_implemented(key)
        assert not is_school_implemented("heal")  # 旧 key 已拆分移除
        assert not is_school_implemented("unknown")

    def test_unknown_school_raises(self):
        with pytest.raises(ValueError):
            get_school_judge("not_exist")

    def test_config_declarations(self):
        rules = get_school_rules()
        assert rules["huiyi_general"].has_keep_pvp
        assert not rules["huiyi_general"].needs_sub_school
        for key in ("huixin_small", "huixin_big"):
            assert rules[key].has_keep_pvp
            assert rules[key].needs_sub_school
            assert rules[key].sub_school_options == {
                "lieshi": "裂石", "pozhu": "破竹", "qiansi": "牵丝",
            }
        for key in ("heal_pure", "heal_fire"):
            heal = rules[key]
            assert not heal.has_keep_pvp
            assert not heal.needs_sub_school
            assert heal.sub_school_options == {}
            assert heal.sub_school_playstyles == {}

    def test_sub_school_playstyles(self):
        rule = get_school_rules()["huixin_big"]
        assert list(rule.sub_school_options) == ["lieshi", "pozhu", "qiansi"]
        playstyles = rule.sub_school_playstyles
        assert list(playstyles["lieshi"]) == ["chuntang", "shuangqie"]
        assert list(playstyles["qiansi"]) == ["zoudi", "feitian"]
        assert "pozhu" not in playstyles  # 破竹无玩法区分

    def test_judge_config_passthrough(self):
        cfg = {"keep_pvp": True, "sub_schools": ["lieshi"],
               "playstyles": {"lieshi": ["chuntang"]}}
        j = get_school_judge("huixin_small", cfg)
        assert j.keep_pvp is True
        assert j.config["sub_schools"] == ["lieshi"]


# ─── 调律潜力判定（check_tuning_worthiness）────────────

class TestTuningWorthiness:
    def test_first_affix_only_top_potential(self, judge):
        # 仅首词条大外，4 个空槽全为万能牌 → 仍可达顶级
        e = make_equip("剑", ["最大外功攻击"])
        assert judge.check_tuning_worthiness(e).rating == Rating.TOP

    def test_huiyi_affix_transmutable_to_top(self, judge):
        # 已出会意破坏顶级条件，但可模拟转律洗掉 → 仍可达顶级
        e = make_equip("剑", ["最大外功攻击", "会意率"])
        assert judge.check_tuning_worthiness(e).rating == Rating.TOP

    def test_double_blemish_excellent_ceiling(self, judge):
        # 会意+大无相 转律只能洗掉一条 → 顶级条件必破，上限优秀
        e = make_equip("剑", ["最大外功攻击", "会意率", "最大无相攻击"])
        assert judge.check_tuning_worthiness(e).rating == Rating.EXCELLENT

    def test_one_huixin_transmutable_to_top(self, judge):
        # 1 条会心对不上槽位，但可转律洗掉 → 仍可达顶级
        e = make_equip("剑", ["最大外功攻击", "会心率"])
        assert judge.check_tuning_worthiness(e).rating == Rating.TOP

    def test_two_rates_usable_ceiling(self, judge):
        # 会心+精准 转律只能洗掉一条，剩余 1 条仍对不上槽位 → 上限能用
        e = make_equip("剑", ["最大外功攻击", "会心率", "精准率"])
        assert judge.check_tuning_worthiness(e).rating == Rating.USABLE

    def test_single_junk_affix_transmutable(self, judge):
        # 剑上出现枪武学增伤：可转律洗掉 → 仍可达顶级
        e = make_equip("剑", ["最大外功攻击", "枪武学增伤"])
        assert judge.check_tuning_worthiness(e).rating == Rating.TOP

    def test_double_junk_affix_ceiling(self, judge):
        # 两条垃圾词条只能转律洗掉一条 → 上限即垃圾
        e = make_equip("剑", ["最大外功攻击", "枪武学增伤", "双刀武学增伤"])
        assert judge.check_tuning_worthiness(e).rating == Rating.JUNK

    def test_junk_wuwei_transmutable_top(self, judge):
        # 大外+大外+劲+体：体可转律洗掉，剩余空槽补剑武学增伤 → 仍可达顶级
        e = make_equip("剑", ["最大外功攻击", "最大外功攻击", "劲", "体"])
        assert judge.check_tuning_worthiness(e).rating == Rating.TOP

    def test_doc_example_qiang_xiaomingjin(self, judge):
        # 文档 04 示例：枪 大外+劲+小鸣金，转律小鸣金 → 有望顶级
        e = make_equip("枪", ["最大外功攻击", "劲", "最小鸣金攻击"])
        assert judge.check_tuning_worthiness(e).rating == Rating.TOP

    def test_tokens_overflow_slots_usable(self, judge):
        # 枪 5 词条：转律洗掉一条后剩余词条仍对不上必选槽 → 能用
        e = make_equip("枪", ["最大外功攻击", "会意率", "最大无相攻击",
                              "精准率", "会心率"])
        assert judge.check_tuning_worthiness(e).rating == Rating.USABLE

    def test_shi_first_weapon_skipped(self, judge):
        # 势首武器：模式只认大外首（势次选已删除）→ 跳过
        e = make_equip("剑", ["势", "剑武学增伤"])
        r = judge.check_tuning_worthiness(e)
        assert r.skipped and not r.not_applicable

    def test_purple_weapon_skipped(self, judge):
        e = make_equip("剑", ["最大外功攻击"], quality="purple")
        assert judge.check_tuning_worthiness(e).skipped

    def test_leg_jin_shi_top_potential(self, judge):
        # 胫甲 劲首+对首领增伤：缺的 劲/势 可由 3 个空槽补 → 仍可达顶级
        e = make_equip("胫甲", ["劲", "对首领单位增伤"], quality="purple")
        assert judge.check_tuning_worthiness(e).rating == Rating.TOP

    def test_misplaced_attr_transmutable(self, judge):
        # 环带 1 条错位无相（废词条）：可转律洗掉 → 仍可达顶级
        e = make_equip("环", ["最大外功攻击", "最大无相攻击"])
        assert judge.check_tuning_worthiness(e).rating == Rating.TOP

    def test_full_equipment_degenerates_to_judge(self, judge):
        # 5 词条时无万能牌，退化为精确匹配
        e = make_equip("剑", ["最大外功攻击", "剑武学增伤", "最大外功攻击", "劲", "势"])
        assert judge.check_tuning_worthiness(e).rating == Rating.TOP

    def test_pvp_player_effect_partial(self, judge, judge_pvp):
        # 对玩家增效：keep_pvp 关闭时可转律洗掉（仍可达顶级但不标记 PVP），
        # 开启时直接顶替对首领增伤槽并标记保留
        e = make_equip("胫甲", ["劲", "对玩家单位增效"], quality="purple")
        r = judge.check_tuning_worthiness(e)
        assert r.rating == Rating.TOP and not r.is_pvp
        r = judge_pvp.check_tuning_worthiness(e)
        assert r.rating == Rating.TOP
        assert r.is_pvp

    def test_unimplemented_school_raises(self):
        # 基类默认潜力判定未实现 → 调用方跳过该流派
        from src.apps.yysls.evaluator import SchoolJudge

        class DummyJudge(SchoolJudge):
            school_name = "占位流派"

            def judge(self, equip):
                raise NotImplementedError

        e = make_equip("剑", ["最大外功攻击"])
        with pytest.raises(NotImplementedError):
            DummyJudge().check_tuning_worthiness(e)

    def test_uncovered_type_not_applicable(self, judge):
        # 扇不在会意流派模式表中 → 标记不适用，而非否决
        e = make_equip("扇", ["最大外功攻击", "劲", "会心率"])
        r = judge.check_tuning_worthiness(e)
        assert r.skipped and r.not_applicable


# ─── 多流派 or 汇总判定（judge_tuning_worthiness）──────

class TestJudgeTuningWorthiness:
    def test_worth_when_huiyi_top_potential(self):
        e = make_equip("剑", ["最大外功攻击"])
        worth, logs = judge_tuning_worthiness(e)
        assert worth
        assert any("会意流派" in line for line in logs)

    def test_not_worth_when_ceiling_usable(self):
        # 会心+精准 转律只能洗掉一条 → 上限能用，不值得
        e = make_equip("剑", ["最大外功攻击", "会心率", "精准率"])
        worth, _ = judge_tuning_worthiness(e)
        assert not worth

    def test_uncovered_type_defaults_to_not_worth(self):
        # 未知部位：所有已实现流派均不适用 → 无法判定，结束调律
        e = make_equip("鱼竿", ["最大外功攻击", "劲"])
        worth, logs = judge_tuning_worthiness(e)
        assert not worth
        assert any("结束调律" in line for line in logs)

    def test_fan_covered_by_huixin(self):
        # 扇：会意不适用，但会心大外流（牵丝副武器）仍可命中 → 值得
        e = make_equip("扇", ["最大外功攻击", "劲", "会心率"])
        worth, logs = judge_tuning_worthiness(e)
        assert worth
        assert any("不适用" in line for line in logs)  # 会意流派
        assert any("会心流派" in line for line in logs)

    def test_fan_with_huiyi_affix_not_worth(self):
        # 扇上 会意率+鸣金属攻 双废词条：转律只能洗掉一条 → 不值得
        e = make_equip("扇", ["最大外功攻击", "劲", "会心率", "会意率",
                              "最大鸣金攻击"])
        worth, _ = judge_tuning_worthiness(e)
        assert not worth

    def test_skipped_quality_is_conclusive_negative(self):
        # 紫色武器：已实现流派给出有效结论（跳过）→ 不值得
        e = make_equip("剑", ["最大外功攻击"], quality="purple")
        worth, logs = judge_tuning_worthiness(e)
        assert not worth
        assert not any("结束调律" in line for line in logs)

    def test_configs_passthrough_keep_pvp(self):
        # 对玩家增效+体 胫甲（限定会意流派）：默认双废词条不值得；
        # keep_pvp 后仅剩 体 可转律洗掉 → 值得
        e = make_equip("胫甲", ["劲", "对玩家单位增效", "体"], quality="purple")
        worth, _ = judge_tuning_worthiness(e, schools=["huiyi_general"])
        assert not worth
        worth, _ = judge_tuning_worthiness(
            e, configs={"huiyi_general": {"keep_pvp": True}},
            schools=["huiyi_general"])
        assert worth

    def test_heal_pure_leg_worth_by_default(self):
        # 同一件胫甲不限定流派：治疗纯奶胫甲本就必需 对玩家增效 → 值得
        e = make_equip("胫甲", ["劲", "对玩家单位增效", "体"], quality="purple")
        worth, logs = judge_tuning_worthiness(e)
        assert worth
        assert any("治疗-纯奶" in line for line in logs)

    def test_schools_filter_limits_participants(self):
        # schools 过滤：仅指定流派参与，会意流派不出现在明细中
        e = make_equip("剑", ["最大外功攻击"])
        worth, logs = judge_tuning_worthiness(e, schools=["huixin_big"])
        assert not any("会意流派" in line for line in logs)
        assert any("会心流派-大外流" in line for line in logs)

    def test_schools_filter_empty_is_inconclusive(self):
        # 空列表：无流派参与 → 无法判定，结束调律
        e = make_equip("剑", ["最大外功攻击"])
        worth, logs = judge_tuning_worthiness(e, schools=[])
        assert not worth
        assert any("结束调律" in line for line in logs)

    def test_schools_none_keeps_default_behavior(self):
        # 默认 None：与不传参时行为一致
        e = make_equip("剑", ["最大外功攻击"])
        assert judge_tuning_worthiness(e, schools=None) == \
            judge_tuning_worthiness(e)
