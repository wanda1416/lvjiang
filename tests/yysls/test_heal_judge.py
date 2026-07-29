"""治疗流派（heal_pure / heal_fire）判定测试

覆盖纯奶（主扇需 扇武学增效）与火拳奶（武器不需增伤）各部位
四档条件、not_together/count_max 语义、两规则独立判定、
胫甲 对玩家单位增效（纯奶池内词条）与调律潜力判定。
"""

import pytest

from src.apps.yysls.equip_parser.models import Affix, EquipmentData
from src.apps.yysls.evaluator import Rating, get_tuning_judge


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
def pure():
    return get_tuning_judge("heal_pure")


@pytest.fixture
def fire():
    return get_tuning_judge("heal_fire")


# ─── 纯奶：主武器（扇，需 扇武学增效） ─────────────────────

class TestPureFan:
    def test_top(self, pure):
        e = make_equip("扇", ["最大外功攻击", "扇武学增效", "最小外功攻击", "劲", "势"])
        assert pure.judge(e).rating == Rating.TOP

    def test_min_first_huixin_excellent(self, pure):
        # 首词条 大外/小外 均可；会心 破坏顶级排除条件 → 优秀
        e = make_equip("扇", ["最小外功攻击", "扇武学增效", "最大外功攻击", "劲", "会心率"])
        assert pure.judge(e).rating == Rating.EXCELLENT

    def test_junk_missing_zengxiao(self, pure):
        e = make_equip("扇", ["最大外功攻击", "最大外功攻击", "最小外功攻击", "劲", "势"])
        r = pure.judge(e)
        assert r.rating == Rating.JUNK
        assert any("增伤" in s for s in r.reasons)


# ─── 纯奶：副武器（伞，不需增伤） ──────────────────────────

class TestPureUmbrella:
    def test_top(self, pure):
        e = make_equip("伞", ["最大外功攻击", "最大外功攻击", "劲", "最小外功攻击", "敏"])
        assert pure.judge(e).rating == Rating.TOP

    def test_min_huixin_together_excellent(self, pure):
        # 敏+会心 同现破坏顶级条件 → 优秀
        e = make_equip("伞", ["最大外功攻击", "最小外功攻击", "劲", "敏", "会心率"])
        assert pure.judge(e).rating == Rating.EXCELLENT

    def test_normal_low_count(self, pure):
        # 大外/小外/劲 计数 ≤ 1 → 一般
        e = make_equip("伞", ["最大外功攻击", "最大外功攻击", "敏", "势", "会心率"])
        assert pure.judge(e).rating == Rating.NORMAL


# ─── 纯奶：环、冠胄、胫甲 ──────────────────────────────────

class TestPureArmor:
    def test_ring_top(self, pure):
        # 无 敏 不触发 敏+会心 同现 → 顶级
        e = make_equip("环", ["最小外功攻击", "全武学增效", "劲", "最大外功攻击", "会心率"])
        assert pure.judge(e).rating == Rating.TOP

    def test_ring_qiansi_excellent(self, pure):
        # 最大牵丝攻击 破坏顶级排除条件 → 优秀
        e = make_equip("环", ["最大外功攻击", "全武学增效", "劲", "敏", "最大牵丝攻击"])
        assert pure.judge(e).rating == Rating.EXCELLENT

    def test_ring_missing_quanwuxue_junk(self, pure):
        e = make_equip("环", ["最小外功攻击", "劲", "最大外功攻击", "敏", "会心率"])
        assert pure.judge(e).rating == Rating.JUNK

    def test_helm_top(self, pure):
        e = make_equip("冠胄", ["会心率", "最大外功攻击", "劲", "敏", "会心率"],
                       quality="purple")
        assert pure.judge(e).rating == Rating.TOP

    def test_helm_normal_low_count(self, pure):
        e = make_equip("冠胄", ["会心率", "最大外功攻击", "敏", "会心率", "势"],
                       quality="purple")
        assert pure.judge(e).rating == Rating.NORMAL

    def test_leg_top_without_keep_pvp(self, pure):
        # 对玩家单位增效 是纯奶池内必选词条：无需开启 keep_pvp 即顶级
        e = make_equip("胫甲", ["劲", "对玩家单位增效", "最小外功攻击", "最大外功攻击", "势"],
                       quality="purple")
        assert pure.judge(e).rating == Rating.TOP

    def test_leg_missing_pvp_effect_junk(self, pure):
        e = make_equip("胫甲", ["劲", "最小外功攻击", "最大外功攻击", "势", "敏"],
                       quality="purple")
        assert pure.judge(e).rating == Rating.JUNK

    def test_leg_boss_damage_junk(self, pure):
        # 纯奶不需要 对首领单位增伤 → 池外 → 垃圾
        e = make_equip("胫甲", ["劲", "对首领单位增伤", "最小外功攻击", "最大外功攻击", "势"],
                       quality="purple")
        assert pure.judge(e).rating == Rating.JUNK


# ─── 火拳奶：武器不需增伤 ──────────────────────────────────

class TestFire:
    def test_fan_top(self, fire):
        e = make_equip("扇", ["最大外功攻击", "最大外功攻击", "劲", "势", "精准率"])
        assert fire.judge(e).rating == Rating.TOP

    def test_fan_normal_missing_min_jin(self, fire):
        # 小外/劲 全缺 → 一般
        e = make_equip("扇", ["最大外功攻击", "最大外功攻击", "势", "敏", "精准率"])
        assert fire.judge(e).rating == Rating.NORMAL

    def test_fan_zengxiao_junk(self, fire):
        # 扇武学增效 不在火拳词条库（两规则独立）→ 垃圾
        e = make_equip("扇", ["最大外功攻击", "扇武学增效", "最大外功攻击", "劲", "势"])
        assert fire.judge(e).rating == Rating.JUNK

    def test_helm_top(self, fire):
        e = make_equip("冠胄", ["会心率", "单体类奇术增伤", "最大外功攻击", "劲", "势"],
                       quality="purple")
        assert fire.judge(e).rating == Rating.TOP

    def test_helm_normal_missing_max(self, fire):
        # 缺 最大外功攻击 → 一般
        e = make_equip("冠胄", ["会心率", "单体类奇术增伤", "劲", "势", "敏"],
                       quality="purple")
        assert fire.judge(e).rating == Rating.NORMAL

    def test_helm_missing_qishu_junk(self, fire):
        e = make_equip("冠胄", ["会心率", "最大外功攻击", "劲", "势", "敏"],
                       quality="purple")
        assert fire.judge(e).rating == Rating.JUNK

    def test_leg_top(self, fire):
        e = make_equip("胫甲", ["劲", "对首领单位增伤", "最大外功攻击", "劲", "势"],
                       quality="purple")
        assert fire.judge(e).rating == Rating.TOP

    def test_leg_missing_boss_damage_junk(self, fire):
        e = make_equip("胫甲", ["劲", "最大外功攻击", "劲", "势", "敏"],
                       quality="purple")
        assert fire.judge(e).rating == Rating.JUNK


# ─── 调律潜力判定 ──────────────────────────────────────────

class TestPotential:
    def test_fan_free_slot_fills_zengxiao_top(self, pure):
        # 空槽可补 扇武学增效 → 仍可达顶级
        e = make_equip("扇", ["最大外功攻击", "最大外功攻击", "劲", "势"])
        assert pure.check_tuning_worthiness(e).rating == Rating.TOP

    def test_fan_full_missing_zengxiao_junk(self, pure):
        # 词条已满缺增效：转律不产生神力词条 → 垃圾
        e = make_equip("扇", ["最大外功攻击", "最大外功攻击", "劲", "势", "敏"])
        assert pure.check_tuning_worthiness(e).rating == Rating.JUNK

    def test_ring_transmute_waste_top(self, pure):
        # 池外词条可被转律洗掉 → 仍可达顶级
        e = make_equip("环", ["最大外功攻击", "最大鸣金攻击", "全武学增效"])
        assert pure.check_tuning_worthiness(e).rating == Rating.TOP
