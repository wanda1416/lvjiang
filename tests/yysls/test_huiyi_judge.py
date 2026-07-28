"""会意通用规则（huiyi_general）判定测试

覆盖各部位三档条件（junk → usable → top 档序）、鸣金属性分部位
（武器写 最大无相攻击 / 非武器写 最大鸣金攻击）、全局 keep_pvp
开关、品阶/首词条筛选、规则注册与调律潜力判定。
裂石/治疗规则测试见 test_lieshi_judge.py / test_heal_judge.py。
"""

import pytest

from src.apps.yysls.equip_parser.models import Affix, EquipmentData
from src.apps.yysls.evaluator import (
    Rating, get_tuning_judge, get_tuning_rules, get_rule_names,
    is_rule_implemented, judge_tuning_worthiness,
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
    return get_tuning_judge("huiyi_general", {"keep_pvp": True})


# ─── 主武器（剑，会意规则需要 剑武学增伤） ─────────────────

class TestJian:
    def test_top(self, judge):
        e = make_equip("剑", ["最大外功攻击", "剑武学增伤", "最大外功攻击", "劲", "势"])
        assert judge.judge(e).rating == Rating.TOP

    def test_excellent_huiyi(self, judge):
        # 会意率 破坏顶级排除条件 → 优秀
        e = make_equip("剑", ["最大外功攻击", "剑武学增伤", "最大外功攻击", "势", "会意率"])
        assert judge.judge(e).rating == Rating.EXCELLENT

    def test_usable_missing_jin_shi(self, judge):
        # 劲/势 全缺 → 能用
        e = make_equip("剑", ["最大外功攻击", "剑武学增伤", "最大外功攻击", "会意率", "最大无相攻击"])
        assert judge.judge(e).rating == Rating.USABLE

    def test_junk_missing_damage(self, judge):
        e = make_equip("剑", ["最大外功攻击", "最大外功攻击", "劲", "势", "会意率"])
        r = judge.judge(e)
        assert r.rating == Rating.JUNK
        assert any("增伤" in s for s in r.reasons)

    def test_junk_wrong_wuxue(self, judge):
        # 枪武学增伤 非本次增伤且池外 → 垃圾
        e = make_equip("剑", ["最大外功攻击", "枪武学增伤", "最大外功攻击", "劲", "势"])
        assert judge.judge(e).rating == Rating.JUNK

    def test_junk_before_usable(self, judge):
        # 会心+精准 双出同时命中 垃圾(≥2) 与 能用(≥1) 条件 → 垃圾优先
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

    def test_usable_missing_jin_shi(self, judge):
        e = make_equip("枪", ["最大外功攻击", "最大外功攻击", "会意率", "最大无相攻击", "最大无相攻击"])
        assert judge.judge(e).rating == Rating.USABLE

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

    def test_usable_single_huixin(self, judge):
        e = make_equip("冠胄", ["会意率", "会心率", "最大外功攻击", "劲", "势"],
                       quality="purple")
        assert judge.judge(e).rating == Rating.USABLE

    def test_junk_double_huixin(self, judge):
        e = make_equip("冠胄", ["会意率", "会心率", "精准率", "最大外功攻击", "劲"],
                       quality="purple")
        assert judge.judge(e).rating == Rating.JUNK


# ─── 胫甲（首词条 劲，必含 对首领单位增伤） ────────────────

class TestLeg:
    def test_top(self, judge):
        e = make_equip("胫甲", ["劲", "对首领单位增伤", "最大外功攻击", "劲", "势"],
                       quality="purple")
        assert judge.judge(e).rating == Rating.TOP

    def test_junk_missing_boss_damage(self, judge):
        e = make_equip("胫甲", ["劲", "最大外功攻击", "最大鸣金攻击", "劲", "势"],
                       quality="purple")
        assert judge.judge(e).rating == Rating.JUNK


# ─── 全局 keep_pvp 开关 ────────────────────────────────────

class TestKeepPvp:
    def test_leg_pvp_off_junk(self, judge):
        # 未开启：对玩家单位增效 池外 → 垃圾
        e = make_equip("胫甲", ["劲", "对玩家单位增效", "最大外功攻击", "劲", "势"],
                       quality="purple")
        assert judge.judge(e).rating == Rating.JUNK

    def test_leg_pvp_on_top(self, judge_pvp):
        # 开启：对玩家单位增效 视作 对首领单位增伤 → 顶级 + PVP 标记
        e = make_equip("胫甲", ["劲", "对玩家单位增效", "最大外功攻击", "劲", "势"],
                       quality="purple")
        r = judge_pvp.judge(e)
        assert r.rating == Rating.TOP
        assert r.is_pvp
        assert any("PVP" in s for s in r.reasons)

    def test_helm_pvp_off_junk(self, judge):
        e = make_equip("冠胄", ["会意率", "单体类奇术增伤", "最大外功攻击", "劲", "势"],
                       quality="purple")
        assert judge.judge(e).rating == Rating.JUNK

    def test_helm_pvp_on_top(self, judge_pvp):
        # 开启：单体类奇术增伤 临时并入词条库 → 顶级 + PVP 标记
        e = make_equip("冠胄", ["会意率", "单体类奇术增伤", "最大外功攻击", "劲", "势"],
                       quality="purple")
        r = judge_pvp.judge(e)
        assert r.rating == Rating.TOP
        assert r.is_pvp


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
            "huiyi_general", "lieshi_small", "lieshi_big",
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
