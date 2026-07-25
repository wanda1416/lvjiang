"""会意流派-通用 判定器测试

覆盖 01-会意流派调律说明.md 第九节全部判定例子，
以及品阶/首词条筛选、keep_pvp、流派注册等边界场景。
"""

import pytest

from src.apps.yysls.equip_parser.models import Affix, EquipmentData
from src.apps.yysls.evaluator import (
    SCHOOL_CLASSES, SCHOOLS, SUB_SCHOOL_PLAYSTYLES, SUB_SCHOOLS,
    Rating, get_school_judge, is_school_implemented,
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

    def test_junk_qiang_wuxue(self, judge):
        e = make_equip("枪", ["最大外功攻击", "枪武学增伤", "最大外功攻击", "劲", "势"])
        assert judge.judge(e).rating == Rating.JUNK


# ─── 第九节判定例子：环、佩 ────────────────────────────────

class TestJewelry:
    def test_top(self, judge):
        e = make_equip("环", ["最大外功攻击", "最大外功攻击", "全武学增效", "劲", "势"])
        assert judge.judge(e).rating == Rating.TOP

    def test_excellent_dawuxiang(self, judge):
        # 大无相在非武器部位即 最大鸣金攻击（大本属）
        e = make_equip("佩", ["最大外功攻击", "最大外功攻击", "全武学增效", "劲", "最大鸣金攻击"])
        assert judge.judge(e).rating == Rating.EXCELLENT

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
        assert any("会心/精准" in s for s in r.reasons)


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

    def test_usable_missing_jin_shi(self, judge):
        e = make_equip("胫甲", ["劲", "对首领单位增伤", "最大外功攻击", "会意率", "最大鸣金攻击"])
        assert judge.judge(e).rating == Rating.USABLE

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

    def test_weapon_shi_first_usable_at_best(self, judge):
        # 武器首词条次选势：通过筛选但模式只认大外(首)，最高能用
        e = make_equip("剑", ["势", "剑武学增伤", "最大外功攻击", "劲", "势"])
        r = judge.judge(e)
        assert not r.skipped
        assert r.rating == Rating.USABLE

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


# ─── 流派注册与空实现 ──────────────────────────────────────

class TestSchools:
    def test_schools_registry(self):
        assert list(SCHOOLS) == [
            "huiyi_general", "huixin_small", "huixin_big",
            "heal_pure", "heal_fire",
        ]

    def test_implemented_flags(self):
        assert is_school_implemented("huiyi_general")
        for key in ("huixin_small", "huixin_big", "heal_pure", "heal_fire"):
            assert not is_school_implemented(key)
        assert not is_school_implemented("unknown")

    def test_unimplemented_judge_raises(self):
        e = make_equip("剑", ["最大外功攻击"])
        for key in ("huixin_small", "huixin_big", "heal_pure", "heal_fire"):
            with pytest.raises(NotImplementedError):
                get_school_judge(key).judge(e)

    def test_unknown_school_raises(self):
        with pytest.raises(ValueError):
            get_school_judge("not_exist")

    def test_config_declarations(self):
        assert SCHOOL_CLASSES["huiyi_general"].has_keep_pvp
        assert not SCHOOL_CLASSES["huiyi_general"].needs_sub_school
        for key in ("huixin_small", "huixin_big"):
            assert SCHOOL_CLASSES[key].has_keep_pvp
            assert SCHOOL_CLASSES[key].needs_sub_school
        for key in ("heal_pure", "heal_fire"):
            assert not SCHOOL_CLASSES[key].has_keep_pvp
            assert not SCHOOL_CLASSES[key].needs_sub_school

    def test_sub_school_playstyles(self):
        assert list(SUB_SCHOOLS) == ["lieshi", "pozhu", "qiansi"]
        assert list(SUB_SCHOOL_PLAYSTYLES["lieshi"]) == ["chuntang", "shuangqie"]
        assert list(SUB_SCHOOL_PLAYSTYLES["qiansi"]) == ["zoudi", "feitian"]
        assert "pozhu" not in SUB_SCHOOL_PLAYSTYLES  # 破竹无玩法区分

    def test_judge_config_passthrough(self):
        cfg = {"keep_pvp": True, "sub_schools": ["lieshi"],
               "playstyles": {"lieshi": ["chuntang"]}}
        j = get_school_judge("huixin_small", cfg)
        assert j.keep_pvp is True
        assert j.config["sub_schools"] == ["lieshi"]
