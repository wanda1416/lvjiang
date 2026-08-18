"""治疗流派（heal_pure / heal_fire）判定测试

覆盖纯奶（主扇需 扇武学增效）与火拳奶（武器不需增伤）各部位
四档条件、not_together/count_max 语义、两规则独立判定、
胫甲 对玩家单位增效（纯奶池内词条）与调律潜力判定。
"""

import pytest

from lvjiang.apps.yysls.core.equip_parser.models import Affix, EquipmentData
from lvjiang.apps.yysls.core.evaluator import Rating, get_tuning_judge


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


# ─── 纯奶：各部位四档判定（参数化） ─────────────────────────

class TestPureRatings:
    """纯奶规则各部位四档评级。"""

    @pytest.mark.parametrize("equip_type,affixes,quality,expected", [
        # 主武器扇
        ("扇", ["最大外功攻击", "扇武学增效", "最小外功攻击", "劲", "敏"], "gold", Rating.TOP),
        ("扇", ["最小外功攻击", "扇武学增效", "最大外功攻击", "劲", "会心率"], "gold", Rating.EXCELLENT),
        # 副武器伞
        ("伞", ["最大外功攻击", "最大外功攻击", "劲", "最小外功攻击", "敏"], "gold", Rating.TOP),
        ("伞", ["最大外功攻击", "最小外功攻击", "劲", "敏", "会心率"], "gold", Rating.EXCELLENT),
        ("伞", ["最大外功攻击", "最大外功攻击", "敏", "势", "会心率"], "gold", Rating.NORMAL),
        # 环
        ("环", ["最小外功攻击", "全武学增效", "劲", "最大外功攻击", "会心率"], "gold", Rating.TOP),
        ("环", ["最大外功攻击", "全武学增效", "劲", "敏", "最大牵丝攻击"], "gold", Rating.EXCELLENT),
        ("环", ["最小外功攻击", "劲", "最大外功攻击", "敏", "会心率"], "gold", Rating.JUNK),
        # 冠胄
        ("冠胄", ["会心率", "最大外功攻击", "劲", "敏", "会心率"], "purple", Rating.TOP),
        ("冠胄", ["会心率", "最大外功攻击", "敏", "会心率", "势"], "purple", Rating.NORMAL),
        # 胫甲
        ("胫甲", ["劲", "对玩家单位增效", "最小外功攻击", "最大外功攻击", "敏"], "purple", Rating.TOP),
        ("胫甲", ["劲", "最小外功攻击", "最大外功攻击", "势", "敏"], "purple", Rating.JUNK),
        ("胫甲", ["劲", "对首领单位增伤", "最小外功攻击", "最大外功攻击", "势"], "purple", Rating.JUNK),
    ])
    def test_rating(self, pure, equip_type, affixes, quality, expected):
        e = make_equip(equip_type, affixes, quality=quality)
        assert pure.judge(e).rating == expected

    def test_junk_missing_zengxiao_reason(self, pure):
        """缺增伤时 reason 包含 '增伤'"""
        e = make_equip("扇", ["最大外功攻击", "最大外功攻击", "最小外功攻击", "劲", "势"])
        r = pure.judge(e)
        assert r.rating == Rating.JUNK
        assert any("增伤" in s for s in r.reasons)


# ─── 火拳奶：各部位四档判定（参数化） ───────────────────────

class TestFireRatings:
    """火拳奶规则各部位四档评级。"""

    @pytest.mark.parametrize("equip_type,affixes,quality,expected", [
        # 扇（不需增伤）
        ("扇", ["最大外功攻击", "最大外功攻击", "劲", "最小外功攻击", "精准率"], "gold", Rating.TOP),
        ("扇", ["最大外功攻击", "最小外功攻击", "敏", "最大无相攻击", "会心率"], "gold", Rating.NORMAL),
        ("扇", ["最大外功攻击", "扇武学增效", "最大外功攻击", "劲", "势"], "gold", Rating.JUNK),
        # 冠胄
        ("冠胄", ["会心率", "单体类奇术增伤", "最大外功攻击", "劲", "敏"], "purple", Rating.TOP),
        ("冠胄", ["会心率", "单体类奇术增伤", "劲", "势", "敏"], "purple", Rating.NORMAL),
        ("冠胄", ["会心率", "最大外功攻击", "劲", "势", "敏"], "purple", Rating.JUNK),
        # 胫甲
        ("胫甲", ["劲", "对首领单位增伤", "最大外功攻击", "劲", "敏"], "purple", Rating.TOP),
        ("胫甲", ["劲", "最大外功攻击", "劲", "势", "敏"], "purple", Rating.JUNK),
    ])
    def test_rating(self, fire, equip_type, affixes, quality, expected):
        e = make_equip(equip_type, affixes, quality=quality)
        assert fire.judge(e).rating == expected


# ─── 势语义：势与三率同现降一般，仅带势/首词条三率封顶优秀 ──────────

class TestShiSemantics:
    def test_pure_fan_shi_without_rate_excellent(self, pure):
        # 武器：带势且无三率同现 → 优秀（封顶，不再顶级）
        e = make_equip("扇", ["最大外功攻击", "扇武学增效", "最小外功攻击", "劲", "势"])
        assert pure.judge(e).rating == Rating.EXCELLENT

    def test_pure_fan_shi_with_rate_normal(self, pure):
        # 武器：势与会心率同现 → 一般
        e = make_equip("扇", ["最大外功攻击", "扇武学增效", "劲", "势", "会心率"])
        assert pure.judge(e).rating == Rating.NORMAL

    def test_pure_leg_shi_only_excellent(self, pure):
        # 腿手：没出现三率只有势 → 优秀
        e = make_equip("胫甲", ["劲", "对玩家单位增效", "最小外功攻击", "最大外功攻击", "势"],
                       quality="purple")
        assert pure.judge(e).rating == Rating.EXCELLENT

    def test_fire_fan_shi_with_rate_normal(self, fire):
        # 武器：势与精准率同现 → 一般
        e = make_equip("扇", ["最大外功攻击", "最大外功攻击", "劲", "势", "精准率"])
        assert fire.judge(e).rating == Rating.NORMAL

    def test_fire_helm_shi_excellent(self, fire):
        # 头：首词条会心率不参与同现判定（include_first 默认 false），
        # 带势 → 优秀
        e = make_equip("冠胄", ["会心率", "单体类奇术增伤", "最大外功攻击", "劲", "势"],
                       quality="purple")
        assert fire.judge(e).rating == Rating.EXCELLENT

    def test_fire_leg_shi_only_excellent(self, fire):
        # 腿手：只有势无三率 → 优秀
        e = make_equip("胫甲", ["劲", "对首领单位增伤", "最大外功攻击", "劲", "势"],
                       quality="purple")
        assert fire.judge(e).rating == Rating.EXCELLENT


# ─── 调律潜力判定 ──────────────────────────────────────────

class TestPotential:
    def test_wrist_full_waste_affix_transmute_is_excellent(self, pure):
        """满词条腕甲带御：考虑一次转律后的潜力为优秀，不是顶级。"""
        e = make_equip(
            "腕甲",
            ["劲", "对玩家单位增效", "最大外功攻击", "最小牵丝攻击", "御"],
        )
        assert pure.check_tuning_worthiness(e).rating == Rating.EXCELLENT

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
