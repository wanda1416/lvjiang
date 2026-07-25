"""治疗流派 判定器测试

覆盖 04-tuning-mechanics.md「治疗流派纯奶/火拳奶各部位词条要求」，
以及子玩法合并择优、部位映射、调律潜力（万能牌+转律模拟）场景。
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
    """默认配置：纯奶 + 火拳奶 全选"""
    return get_school_judge("heal")


@pytest.fixture
def pure():
    return get_school_judge("heal", {"sub_schools": ["pure"]})


@pytest.fixture
def fire():
    return get_school_judge("heal", {"sub_schools": ["fire"]})


# ─── 主武器（扇） ──────────────────────────────────────────

class TestMainWeapon:
    def test_pure_top(self, heal):
        e = make_equip("扇", ["最大外功攻击", "扇武学增效", "劲",
                              "最小外功攻击", "劲"])
        r = heal.judge(e)
        assert r.rating == Rating.TOP
        assert any("纯奶" in s for s in r.reasons)

    def test_pure_excellent_with_wuxiang(self, heal):
        # 可选槽被 大无相 占据：命中模式但顶级条件破 → 优秀
        e = make_equip("扇", ["最大外功攻击", "扇武学增效", "劲",
                              "最小外功攻击", "最大无相攻击"])
        assert heal.judge(e).rating == Rating.EXCELLENT

    def test_fire_top_without_damage(self, heal):
        # 火拳奶主武器无增伤要求；纯奶视角缺扇武学增效 → 取火拳最优
        e = make_equip("扇", ["最大外功攻击", "劲", "劲", "敏", "敏"])
        r = heal.judge(e)
        assert r.rating == Rating.TOP
        assert any("火拳奶" in s for s in r.reasons)

    def test_pure_only_junk_without_damage(self, pure):
        # 仅勾选纯奶：缺失扇武学增效直接垃圾
        e = make_equip("扇", ["最大外功攻击", "劲", "劲", "敏", "敏"])
        r = pure.judge(e)
        assert r.rating == Rating.JUNK
        assert any("增伤" in s for s in r.reasons)

    def test_main_pool_has_no_huixin(self, heal):
        # 会心是池内词条但主武器无槽位 → 模式未命中，能用
        e = make_equip("扇", ["最大外功攻击", "扇武学增效", "劲",
                              "劲", "会心率"])
        assert heal.judge(e).rating == Rating.USABLE

    def test_first_affix_mismatch_skipped(self, heal):
        e = make_equip("扇", ["劲", "扇武学增效", "劲", "最大外功攻击", "劲"])
        r = heal.judge(e)
        assert r.skipped and not r.not_applicable

    def test_purple_weapon_skipped(self, heal):
        e = make_equip("扇", ["最大外功攻击", "扇武学增效", "劲"],
                       quality="purple")
        assert heal.judge(e).skipped


# ─── 副武器（伞）与首饰 ────────────────────────────────────

class TestSubWeaponAndRing:
    def test_umbrella_top(self, heal):
        e = make_equip("伞", ["最小外功攻击", "劲", "最大外功攻击",
                              "劲", "敏"])
        assert heal.judge(e).rating == Rating.TOP

    def test_umbrella_min_huixin_excellent(self, heal):
        # 敏和会心同时出现 → 顶级条件破，优秀
        e = make_equip("伞", ["最小外功攻击", "劲", "最大外功攻击",
                              "会心率", "敏"])
        assert heal.judge(e).rating == Rating.EXCELLENT

    def test_umbrella_wuxue_junk(self, heal):
        # 两个玩法均不需要伞武学增伤 → 垃圾
        e = make_equip("伞", ["最大外功攻击", "伞武学增伤", "劲",
                              "劲", "敏"])
        assert heal.judge(e).rating == Rating.JUNK

    def test_ring_pure_needs_quanwuxue(self, heal):
        # 纯奶环需要全武学增效；火拳奶不需要（全武学在火拳视角是垃圾）
        e = make_equip("环", ["最大外功攻击", "全武学增效", "劲",
                              "劲", "会心率"])
        r = heal.judge(e)
        assert r.rating == Rating.TOP
        assert any("纯奶" in s for s in r.reasons)

    def test_ring_fire_without_quanwuxue(self, heal):
        e = make_equip("佩", ["最大外功攻击", "劲", "最小外功攻击",
                              "敏", "劲"])
        r = heal.judge(e)
        assert r.rating == Rating.TOP
        assert any("火拳奶" in s for s in r.reasons)

    def test_ring_qiansi_attack_is_wuxiang(self, heal):
        # 非武器部位 最大牵丝攻击 视作大无相：命中模式但顶级破 → 优秀
        e = make_equip("环", ["最大外功攻击", "全武学增效", "劲",
                              "最大牵丝攻击", "劲"])
        assert heal.judge(e).rating == Rating.EXCELLENT


# ─── 防具 ──────────────────────────────────────────────────

class TestArmor:
    def test_helm_pure_top(self, heal):
        e = make_equip("冠胄", ["会心率", "劲", "最大外功攻击",
                                "劲", "最小外功攻击"], quality="purple")
        assert heal.judge(e).rating == Rating.TOP

    def test_helm_fire_needs_qishu(self, fire):
        # 火拳奶头盔必需 单体奇术增伤
        e = make_equip("胸甲", ["会心率", "单体类奇术增伤", "劲",
                                "最大外功攻击", "劲"])
        assert fire.judge(e).rating == Rating.TOP
        e2 = make_equip("胸甲", ["会心率", "劲", "最大外功攻击",
                                 "劲", "敏"])
        r = fire.judge(e2)
        assert r.rating == Rating.JUNK
        assert any("增伤" in s for s in r.reasons)

    def test_leg_pure_needs_player_effect(self, pure):
        # 纯奶胫甲必需 对玩家增效（核心词条而非 PVP 保留）
        e = make_equip("胫甲", ["劲", "对玩家单位增效", "最大外功攻击",
                                "劲", "敏"])
        r = pure.judge(e)
        assert r.rating == Rating.TOP
        assert not r.is_pvp

    def test_leg_fire_needs_boss_damage(self, heal):
        e = make_equip("腕甲", ["劲", "对首领单位增伤", "劲",
                                "敏", "会心率"])
        r = heal.judge(e)
        assert r.rating == Rating.EXCELLENT  # 敏+会心同时出现
        assert any("火拳奶" in s for s in r.reasons)

    def test_uncovered_weapon_not_applicable(self, heal):
        e = make_equip("剑", ["最大外功攻击", "劲", "劲"])
        r = heal.judge(e)
        assert r.skipped and r.not_applicable


# ─── 调律潜力（万能牌 + 转律模拟） ─────────────────────────

class TestTuningWorthiness:
    def test_partial_top_potential(self, heal):
        e = make_equip("扇", ["最大外功攻击", "扇武学增效", "劲"])
        r = heal.check_tuning_worthiness(e)
        assert r.rating == Rating.TOP

    def test_pure_only_missing_damage_fillable(self, pure):
        # 缺扇武学增效但仍有空槽 → 万能牌可补足
        e = make_equip("扇", ["最大外功攻击", "劲", "劲"])
        assert pure.check_tuning_worthiness(e).rating == Rating.TOP

    def test_pure_only_missing_damage_full_slots(self, pure):
        # 词条已满且缺扇武学增效：转律不产神力 → 上限能用
        e = make_equip("扇", ["最大外功攻击", "劲", "劲", "敏", "敏"])
        assert pure.check_tuning_worthiness(e).rating == Rating.USABLE

    def test_junk_affix_transmutable(self, heal):
        # 单条废词条（势 不在治疗词条池）可被转律洗掉
        e = make_equip("伞", ["最大外功攻击", "劲", "势"])
        r = heal.check_tuning_worthiness(e)
        assert r.rating == Rating.TOP
        assert any("模拟转律" in s for s in r.reasons)

    def test_double_junk_ceiling(self, heal):
        # 双废词条只能洗一条 → 垃圾
        e = make_equip("伞", ["最大外功攻击", "劲", "势", "会意率"])
        assert heal.check_tuning_worthiness(e).rating == Rating.JUNK

    def test_wuxiang_transmutable_to_top(self, heal):
        # 大无相破顶级条件，转律洗掉后仍可达顶级
        e = make_equip("伞", ["最大外功攻击", "劲", "最大无相攻击", "劲"])
        r = heal.check_tuning_worthiness(e)
        assert r.rating == Rating.TOP
        assert any("模拟转律" in s for s in r.reasons)

    def test_or_judgment_includes_heal(self):
        # 扇（会意不适用）由治疗流派给出值得结论
        e = make_equip("扇", ["最大外功攻击", "扇武学增效"])
        worth, logs = judge_tuning_worthiness(e, schools=["heal"])
        assert worth
        assert any("治疗流派" in line for line in logs)
