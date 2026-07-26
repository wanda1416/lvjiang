"""治疗流派（纯奶/火拳奶）判定器测试（通用规则引擎）

覆盖 04-治疗纯奶调律说明.md / 05-治疗火拳奶调律说明.md
第九节全部判定例子，以及双变体择优标签、品阶/首词条筛选、
调律潜力判定等场景。
"""

import pytest

from src.apps.yysls.equip_parser.models import Affix, EquipmentData
from src.apps.yysls.evaluator import (
    Rating, get_school_judge, judge_tuning_worthiness,
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
def heal():
    """未限定玩法（纯奶 + 火拳奶均激活，取评级最高）"""
    return get_school_judge("heal")


@pytest.fixture
def pure():
    return get_school_judge("heal", {"sub_schools": ["pure"]})


@pytest.fixture
def fire():
    return get_school_judge("heal", {"sub_schools": ["fire"]})


# ─── 04 文档第九节判定例子（纯奶） ─────────────────────────

class TestPureDocExamples:
    def test_1_fan_top(self, pure):
        # 势 在纯奶词条库内且不被顶级条件排除
        e = make_equip("扇", ["最大外功攻击", "扇武学增效", "最小外功攻击", "劲", "势"])
        assert pure.judge(e).rating == Rating.TOP

    def test_2_fan_huixin_excellent(self, pure):
        # 主武器首词条 大外/小外 均可；会心破坏顶级条件 → 优秀
        e = make_equip("扇", ["最小外功攻击", "扇武学增效", "最大外功攻击", "劲", "会心率"])
        assert pure.judge(e).rating == Rating.EXCELLENT

    def test_3_umbrella_min_huixin_excellent(self, pure):
        # 敏+会心 同时出现 → 优秀
        e = make_equip("伞", ["最大外功攻击", "最小外功攻击", "劲", "敏", "会心率"])
        assert pure.judge(e).rating == Rating.EXCELLENT

    def test_4_umbrella_top(self, pure):
        e = make_equip("伞", ["最大外功攻击", "最大外功攻击", "劲", "最小外功攻击", "敏"])
        assert pure.judge(e).rating == Rating.TOP

    def test_5_ring_top(self, pure):
        # 无 敏 不触发 敏+会心 同现 → 顶级
        e = make_equip("环", ["最小外功攻击", "全武学增效", "劲", "最大外功攻击", "会心率"])
        assert pure.judge(e).rating == Rating.TOP

    def test_6_ring_own_attr_excellent(self, pure):
        # 最大牵丝攻击 → 大本属（大无相）→ 优秀
        e = make_equip("环", ["最大外功攻击", "全武学增效", "劲", "敏", "最大牵丝攻击"])
        assert pure.judge(e).rating == Rating.EXCELLENT

    def test_7_helm_double_huixin_top(self, pure):
        e = make_equip("冠胄", ["会心率", "最大外功攻击", "劲", "敏", "会心率"],
                       quality="purple")
        assert pure.judge(e).rating == Rating.TOP

    def test_8_leg_top(self, pure):
        e = make_equip("胫甲", ["劲", "对玩家单位增效", "最小外功攻击", "最大外功攻击", "势"],
                       quality="purple")
        assert pure.judge(e).rating == Rating.TOP

    def test_9_leg_boss_damage_junk(self, pure):
        # 纯奶胫甲不需要 对首领单位增伤 → 废神力词条 → 垃圾
        e = make_equip("胫甲", ["劲", "对首领单位增伤", "最大外功攻击", "劲", "敏"],
                       quality="purple")
        assert pure.judge(e).rating == Rating.JUNK

    def test_10_wrist_jingzhun_junk(self, pure):
        # 精准 不在纯奶词条库 → 垃圾
        e = make_equip("腕甲", ["劲", "对玩家单位增效", "最大外功攻击", "精准率", "敏"],
                       quality="purple")
        assert pure.judge(e).rating == Rating.JUNK


# ─── 05 文档第九节判定例子（火拳奶） ───────────────────────

class TestFireDocExamples:
    def test_1_fan_top(self, fire):
        e = make_equip("扇", ["最大外功攻击", "最大外功攻击", "劲", "最小外功攻击", "势"])
        assert fire.judge(e).rating == Rating.TOP

    def test_2_fan_zengxiao_junk(self, fire):
        # 火拳奶不需要 扇武学增效 → 废神力词条 → 垃圾
        e = make_equip("扇", ["最大外功攻击", "扇武学增效", "最大外功攻击", "劲", "敏"])
        assert fire.judge(e).rating == Rating.JUNK

    def test_3_umbrella_min_huixin_excellent(self, fire):
        e = make_equip("伞", ["最大外功攻击", "最大外功攻击", "最小外功攻击", "敏", "会心率"])
        assert fire.judge(e).rating == Rating.EXCELLENT

    def test_4_ring_top(self, fire):
        # 精准 在火拳奶词条库内且不被顶级条件排除
        e = make_equip("环", ["最大外功攻击", "最大外功攻击", "劲", "精准率", "会心率"])
        assert fire.judge(e).rating == Rating.TOP

    def test_5_ring_quanwuxue_junk(self, fire):
        # 火拳奶环不需要 全武学增效 → 垃圾
        e = make_equip("环", ["最大外功攻击", "全武学增效", "最大外功攻击", "劲", "敏"])
        assert fire.judge(e).rating == Rating.JUNK

    def test_6_helm_top(self, fire):
        e = make_equip("冠胄", ["会心率", "单体类奇术增伤", "最大外功攻击", "劲", "会心率"],
                       quality="purple")
        assert fire.judge(e).rating == Rating.TOP

    def test_7_helm_own_attr_excellent(self, fire):
        e = make_equip("冠胄", ["会心率", "单体类奇术增伤", "最大外功攻击", "敏", "最大牵丝攻击"],
                       quality="purple")
        assert fire.judge(e).rating == Rating.EXCELLENT

    def test_8_leg_top(self, fire):
        e = make_equip("胫甲", ["劲", "对首领单位增伤", "最大外功攻击", "劲", "势"],
                       quality="purple")
        assert fire.judge(e).rating == Rating.TOP

    def test_9_leg_jingzhun_excellent(self, fire):
        # 胫甲顶级条件排除 会心/精准 → 优秀
        e = make_equip("胫甲", ["劲", "对首领单位增伤", "最大外功攻击", "敏", "精准率"],
                       quality="purple")
        assert fire.judge(e).rating == Rating.EXCELLENT

    def test_10_wrist_wuxiang_junk(self, fire):
        # 防具上 最小无相攻击 属错位属攻 → 垃圾
        e = make_equip("腕甲", ["劲", "对首领单位增伤", "最大外功攻击", "最小无相攻击", "敏"],
                       quality="purple")
        assert fire.judge(e).rating == Rating.JUNK


# ─── 双变体择优与标签 ──────────────────────────────────────

class TestVariantSelection:
    def test_fan_pure_top_with_label(self, heal):
        e = make_equip("扇", ["最大外功攻击", "扇武学增效", "劲", "最小外功攻击", "劲"])
        r = heal.judge(e)
        assert r.rating == Rating.TOP
        assert any(s.startswith("[纯奶]") for s in r.reasons)

    def test_fan_fire_top_with_label(self, heal):
        # 纯奶主武器缺 扇武学增效 → 垃圾；火拳奶命中 → 择优火拳奶
        e = make_equip("扇", ["最大外功攻击", "最大外功攻击", "劲", "敏", "敏"])
        r = heal.judge(e)
        assert r.rating == Rating.TOP
        assert any(s.startswith("[火拳奶]") for s in r.reasons)

    def test_ring_pure_top(self, heal):
        e = make_equip("环", ["最大外功攻击", "全武学增效", "劲", "劲", "会心率"])
        r = heal.judge(e)
        assert r.rating == Rating.TOP
        assert any(s.startswith("[纯奶]") for s in r.reasons)

    def test_pendant_fire_top(self, heal):
        # 佩归并为环：纯奶缺 全武学增效 → 垃圾；火拳奶命中
        e = make_equip("佩", ["最大外功攻击", "最大外功攻击", "劲", "敏", "劲"])
        r = heal.judge(e)
        assert r.rating == Rating.TOP
        assert any(s.startswith("[火拳奶]") for s in r.reasons)


# ─── 筛选与边界 ────────────────────────────────────────────

class TestScreening:
    def test_jin_first_fan_skipped(self, heal):
        e = make_equip("扇", ["劲", "最大外功攻击", "劲", "敏", "势"])
        r = heal.judge(e)
        assert r.skipped and not r.not_applicable

    def test_purple_weapon_skipped(self, heal):
        e = make_equip("扇", ["最大外功攻击", "扇武学增效", "劲", "最小外功攻击", "劲"],
                       quality="purple")
        assert heal.judge(e).skipped

    def test_jian_not_applicable(self, heal):
        e = make_equip("剑", ["最大外功攻击", "剑武学增伤", "最大外功攻击", "劲", "势"])
        r = heal.judge(e)
        assert r.skipped and r.not_applicable

    def test_leg_player_effect_not_pvp_flag(self, pure):
        # 对玩家增效是纯奶胫甲的必需词条而非 PVP 保留（heal 无 keep_pvp）
        e = make_equip("胫甲", ["劲", "对玩家单位增效", "最大外功攻击", "劲", "敏"],
                       quality="purple")
        r = pure.judge(e)
        assert r.rating == Rating.TOP
        assert not r.is_pvp


# ─── 调律潜力判定 ──────────────────────────────────────────

class TestTuningWorthiness:
    def test_fan_with_damage_top(self, heal):
        e = make_equip("扇", ["最大外功攻击", "扇武学增效", "劲"])
        assert heal.check_tuning_worthiness(e).rating == Rating.TOP

    def test_pure_fan_empty_slots_supply_damage(self, pure):
        # 缺 扇武学增效 但有 2 个空槽可补 → 仍可达顶级
        e = make_equip("扇", ["最大外功攻击", "劲", "劲"])
        assert pure.check_tuning_worthiness(e).rating == Rating.TOP

    def test_pure_fan_full_slots_usable(self, pure):
        # 满槽缺增伤：转律不产生神力词条 → 上限能用
        e = make_equip("扇", ["最大外功攻击", "劲", "劲", "敏", "敏"])
        assert pure.check_tuning_worthiness(e).rating == Rating.USABLE

    def test_junk_affix_transmutable(self, heal):
        # 会意率 在两变体库外，可转律洗掉 → 仍可达顶级
        e = make_equip("伞", ["最大外功攻击", "劲", "会意率"])
        r = heal.check_tuning_worthiness(e)
        assert r.rating == Rating.TOP
        assert any("模拟转律" in s for s in r.reasons)

    def test_double_junk_ceiling(self, heal):
        e = make_equip("伞", ["最大外功攻击", "劲", "会意率", "最大鸣金攻击"])
        assert heal.check_tuning_worthiness(e).rating == Rating.JUNK

    def test_dawuxiang_transmutable_top(self, heal):
        # 大无相破坏顶级条件，可转律洗掉 → 仍可达顶级
        e = make_equip("伞", ["最大外功攻击", "劲", "最大无相攻击", "劲"])
        r = heal.check_tuning_worthiness(e)
        assert r.rating == Rating.TOP
        assert any("模拟转律" in s for s in r.reasons)

    def test_worthiness_via_heal(self):
        e = make_equip("扇", ["最大外功攻击", "扇武学增效"])
        worth, logs = judge_tuning_worthiness(e, schools=["heal"])
        assert worth
        assert any("治疗流派" in line for line in logs)
