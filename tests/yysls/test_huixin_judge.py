"""会心流派（大外流/小外流）判定器测试（通用规则引擎）

覆盖 02-会心大外流调律说明.md / 03-会心小外流调律说明.md
第九节全部判定例子，以及多武器角色择优、子流派/玩法配置、
keep_pvp、小外属归一化等场景。
"""

import pytest

from src.apps.yysls.equip_parser.models import Affix, EquipmentData
from src.apps.yysls.evaluator import Rating, get_school_judge


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
def big():
    """大外流：未限定子流派（默认全部启用）"""
    return get_school_judge("huixin_big")


@pytest.fixture
def big_lieshi():
    return get_school_judge("huixin_big", {"sub_schools": ["lieshi"]})


@pytest.fixture
def small():
    return get_school_judge("huixin_small")


@pytest.fixture
def small_lieshi():
    return get_school_judge("huixin_small", {"sub_schools": ["lieshi"]})


# ─── 02 文档第九节判定例子（大外流，裂石） ─────────────────

class TestBigDocExamples:
    def test_1_modao_main_top(self, big_lieshi):
        e = make_equip("陌刀", ["最大外功攻击", "陌刀武学增伤", "最大外功攻击", "劲", "敏"])
        assert big_lieshi.judge(e).rating == Rating.TOP

    def test_2_modao_huixin_excellent(self, big_lieshi):
        e = make_equip("陌刀", ["最大外功攻击", "陌刀武学增伤", "最大外功攻击", "劲", "会心率"])
        assert big_lieshi.judge(e).rating == Rating.EXCELLENT

    def test_3_hengdao_sub_top(self, big_lieshi):
        # 横刀兼任纯唐主武器（缺唐横刀增伤→垃圾）与双切副武器，择优取副武器 TOP
        e = make_equip("横刀", ["最大外功攻击", "最大外功攻击", "劲", "敏", "会心率"])
        assert big_lieshi.judge(e).rating == Rating.TOP

    def test_4_hengdao_sub_shi_top(self, big_lieshi):
        # 副武器顶级条件不排除 势/会心
        e = make_equip("横刀", ["最大外功攻击", "最大外功攻击", "劲", "势", "会心率"])
        assert big_lieshi.judge(e).rating == Rating.TOP

    def test_5_ring_top(self, big_lieshi):
        e = make_equip("环", ["最大外功攻击", "全武学增效", "最大外功攻击", "劲", "敏"])
        assert big_lieshi.judge(e).rating == Rating.TOP

    def test_6_ring_own_attr_excellent(self, big_lieshi):
        # 最大裂石攻击 → 大本属（大无相），命中模式但顶级条件排除 → 优秀
        e = make_equip("环", ["最大外功攻击", "全武学增效", "最大外功攻击", "劲", "最大裂石攻击"])
        assert big_lieshi.judge(e).rating == Rating.EXCELLENT

    def test_7_helm_one_jingzhun_top(self, big_lieshi):
        # 会心首 + 1 条精准：精准计数（含首）= 1 ≤ 1 → 顶级
        e = make_equip("冠胄", ["会心率", "最大外功攻击", "劲", "敏", "精准率"],
                       quality="purple")
        assert big_lieshi.judge(e).rating == Rating.TOP

    def test_8_helm_double_jingzhun_excellent(self, big_lieshi):
        # 精准首 + 1 条精准：计数（含首）= 2 > 1 → 优秀
        e = make_equip("冠胄", ["精准率", "最大外功攻击", "劲", "敏", "精准率"],
                       quality="purple")
        assert big_lieshi.judge(e).rating == Rating.EXCELLENT

    def test_9_leg_top(self, big_lieshi):
        e = make_equip("胫甲", ["劲", "对首领单位增伤", "最大外功攻击", "劲", "敏"],
                       quality="purple")
        assert big_lieshi.judge(e).rating == Rating.TOP

    def test_10_leg_huiyi_junk(self, big_lieshi):
        # 会意率 不在大外流词条库 → 垃圾
        e = make_equip("胫甲", ["劲", "对首领单位增伤", "最大外功攻击", "劲", "会意率"],
                       quality="purple")
        assert big_lieshi.judge(e).rating == Rating.JUNK


# ─── 03 文档第九节判定例子（小外流，裂石） ─────────────────

class TestSmallDocExamples:
    def test_1_modao_main_top(self, small_lieshi):
        e = make_equip("陌刀", ["最小外功攻击", "陌刀武学增伤", "最小外功攻击", "敏", "最大无相攻击"])
        assert small_lieshi.judge(e).rating == Rating.TOP

    def test_2_modao_jingzhun_excellent(self, small_lieshi):
        e = make_equip("陌刀", ["最小外功攻击", "陌刀武学增伤", "最小外功攻击", "敏", "精准率"])
        assert small_lieshi.judge(e).rating == Rating.EXCELLENT

    def test_3_hengdao_sub_top(self, small_lieshi):
        e = make_equip("横刀", ["最小外功攻击", "最小外功攻击", "敏", "最大无相攻击", "会心率"])
        assert small_lieshi.judge(e).rating == Rating.TOP

    def test_4_hengdao_xiaowuxiang_excellent(self, small_lieshi):
        # 武器上 最小无相攻击 → 小无相（库内），但顶级条件排除 → 优秀
        e = make_equip("横刀", ["最小外功攻击", "最小外功攻击", "敏", "会心率", "最小无相攻击"])
        assert small_lieshi.judge(e).rating == Rating.EXCELLENT

    def test_5_ring_dawai_junk(self, small_lieshi):
        # 最大外功攻击 不在小外流词条库 → 垃圾
        e = make_equip("环", ["最小外功攻击", "全武学增效", "最小外功攻击", "敏", "最大外功攻击"])
        assert small_lieshi.judge(e).rating == Rating.JUNK

    def test_6_helm_double_huixin_top(self, small_lieshi):
        # 会心不限条数；最大裂石攻击 → 大本属（大无相）填必选槽
        e = make_equip("冠胄", ["会心率", "最小外功攻击", "敏", "最大裂石攻击", "会心率"],
                       quality="purple")
        assert small_lieshi.judge(e).rating == Rating.TOP

    def test_7_helm_double_jingzhun_excellent(self, small_lieshi):
        e = make_equip("冠胄", ["精准率", "最小外功攻击", "最大裂石攻击", "会心率", "精准率"],
                       quality="purple")
        assert small_lieshi.judge(e).rating == Rating.EXCELLENT

    def test_8_leg_jingzhun_first_top(self, small_lieshi):
        # 胫甲首词条 会心/精准 均可；非首词条无 会心/精准 → 顶级
        e = make_equip("胫甲", ["精准率", "对首领单位增伤", "最小外功攻击", "敏", "最大裂石攻击"],
                       quality="purple")
        assert small_lieshi.judge(e).rating == Rating.TOP

    def test_9_leg_huixin_body_excellent(self, small_lieshi):
        e = make_equip("胫甲", ["会心率", "对首领单位增伤", "最小外功攻击", "敏", "会心率"],
                       quality="purple")
        assert small_lieshi.judge(e).rating == Rating.EXCELLENT

    def test_10_wrist_jin_junk(self, small_lieshi):
        # 劲 不在小外流词条库 → 垃圾
        e = make_equip("腕甲", ["会心率", "对首领单位增伤", "最小外功攻击", "劲", "敏"],
                       quality="purple")
        assert small_lieshi.judge(e).rating == Rating.JUNK


# ─── 多武器角色与子流派/玩法配置 ───────────────────────────

class TestWeaponRoles:
    def test_role_label_in_reasons(self, big_lieshi):
        e = make_equip("陌刀", ["最大外功攻击", "陌刀武学增伤", "最大外功攻击", "劲", "敏"])
        r = big_lieshi.judge(e)
        assert any(s.startswith("[裂石-双切 主武器]") for s in r.reasons)

    def test_main_weapon_missing_damage_junk(self, big):
        # 双刀仅破竹主武器角色：缺 双刀武学增伤 → 垃圾
        e = make_equip("双刀", ["最大外功攻击", "最大外功攻击", "劲", "敏", "会心率"])
        r = big.judge(e)
        assert r.rating == Rating.JUNK
        assert any("增伤" in s for s in r.reasons)

    def test_fan_as_qiansi_sub_top(self, big):
        # 扇：牵丝副武器角色命中 → 顶级
        e = make_equip("扇", ["最大外功攻击", "最大外功攻击", "劲", "敏", "会心率"])
        assert big.judge(e).rating == Rating.TOP

    def test_fan_not_applicable_when_lieshi_only(self, big_lieshi):
        # 仅勾选裂石时扇无角色 → 不适用
        e = make_equip("扇", ["最大外功攻击", "最大外功攻击", "劲", "敏", "会心率"])
        r = big_lieshi.judge(e)
        assert r.skipped and r.not_applicable

    def test_playstyle_filter(self):
        # 仅勾选纯唐玩法：陌刀只剩副武器角色
        j = get_school_judge("huixin_big", {
            "sub_schools": ["lieshi"],
            "playstyles": {"lieshi": ["chuntang"]},
        })
        e = make_equip("陌刀", ["最大外功攻击", "最大外功攻击", "劲", "敏", "会心率"])
        assert j.judge(e).rating == Rating.TOP
        # 主武学增伤在副武器角色下反而是废词条 → 垃圾
        e = make_equip("陌刀", ["最大外功攻击", "陌刀武学增伤", "最大外功攻击", "劲", "敏"])
        assert j.judge(e).rating == Rating.JUNK

    def test_shengbiao_small_sub_top(self, small):
        e = make_equip("绳镖", ["最小外功攻击", "最小外功攻击", "敏", "最大无相攻击", "会心率"])
        assert small.judge(e).rating == Rating.TOP

    def test_wrong_first_skipped_not_na(self, small):
        # 大外首在小外流不符合首词条要求 → 跳过（并非不适用）
        e = make_equip("绳镖", ["最大外功攻击", "最小外功攻击", "敏", "最大无相攻击", "会心率"])
        r = small.judge(e)
        assert r.skipped and not r.not_applicable


# ─── 属攻归一化 ────────────────────────────────────────────

class TestAttrNormalization:
    def test_armor_own_attr_as_dawuxiang(self, big):
        # 未限定子流派：牵丝也是大本属 → 大无相命中模式但破坏顶级条件
        e = make_equip("冠胄", ["会心率", "精准率", "最大外功攻击", "劲", "最大牵丝攻击"],
                       quality="purple")
        assert big.judge(e).rating == Rating.EXCELLENT

    def test_armor_other_school_attr_junk(self, big_lieshi):
        # 仅勾选裂石：牵丝大属攻属错位 → 垃圾
        e = make_equip("冠胄", ["会心率", "精准率", "最大外功攻击", "劲", "最大牵丝攻击"],
                       quality="purple")
        assert big_lieshi.judge(e).rating == Rating.JUNK

    def test_small_ring_xiaowaishu_excellent(self, small_lieshi):
        # 最小鸣金攻击（他流派小属攻）→ 小外属：命中模式但顶级条件排除 → 优秀
        e = make_equip("环", ["最小外功攻击", "全武学增效", "最小外功攻击", "敏", "最小鸣金攻击"])
        assert small_lieshi.judge(e).rating == Rating.EXCELLENT

    def test_small_ring_full_slots_top(self, small):
        e = make_equip("环", ["最小外功攻击", "最小外功攻击", "敏", "全武学增效", "最大裂石攻击"])
        assert small.judge(e).rating == Rating.TOP


# ─── keep_pvp ──────────────────────────────────────────────

class TestKeepPvp:
    def test_leg_player_effect(self, big):
        e = make_equip("胫甲", ["劲", "对玩家单位增效", "最大外功攻击", "劲", "敏"],
                       quality="purple")
        assert big.judge(e).rating == Rating.JUNK
        j = get_school_judge("huixin_big", {"keep_pvp": True})
        r = j.judge(e)
        assert r.rating == Rating.TOP
        assert r.is_pvp

    def test_helm_qishu_kept(self):
        j = get_school_judge("huixin_big", {"keep_pvp": True})
        e = make_equip("冠胄", ["会心率", "最大外功攻击", "劲", "敏", "单体类奇术增伤"],
                       quality="purple")
        r = j.judge(e)
        assert r.rating == Rating.USABLE
        assert r.is_pvp


# ─── 调律潜力判定 ──────────────────────────────────────────

class TestTuningWorthiness:
    def test_double_junk_ceiling(self, big):
        # 鸣金全称废词条 + 小无相不在大外流词条库：转律只能洗一条 → 垃圾
        e = make_equip("环", ["最大外功攻击", "最大鸣金攻击", "最小牵丝攻击"])
        assert big.check_tuning_worthiness(e).rating == Rating.JUNK

    def test_fan_double_junk(self, big):
        e = make_equip("扇", ["最大外功攻击", "劲", "会意率", "最大鸣金攻击"])
        assert big.check_tuning_worthiness(e).rating == Rating.JUNK

    def test_single_junk_transmutable_top(self, big):
        e = make_equip("环", ["最大外功攻击", "最大鸣金攻击"])
        assert big.check_tuning_worthiness(e).rating == Rating.TOP

    def test_fan_partial_top(self, big):
        e = make_equip("扇", ["最大外功攻击", "劲", "会心率"])
        assert big.check_tuning_worthiness(e).rating == Rating.TOP

    def test_fan_not_applicable_when_lieshi_only(self, big_lieshi):
        e = make_equip("扇", ["最大外功攻击", "劲", "会心率"])
        r = big_lieshi.check_tuning_worthiness(e)
        assert r.skipped and r.not_applicable

    def test_small_leg_partial_top(self, small):
        # 首词条会心 + 1 条小外，空槽可补对首领增伤 → 仍可达顶级
        e = make_equip("胫甲", ["会心率", "最小外功攻击"], quality="purple")
        assert small.check_tuning_worthiness(e).rating == Rating.TOP
