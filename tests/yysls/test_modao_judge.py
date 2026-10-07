"""威威大王（huixin_modao）判定测试

覆盖五条判定契约：
1. 敏 / 最小外功攻击 为缺陷词条——出现任一条 → 一般封顶，两条 → 垃圾；
2. 会意率本身不封顶，但与 精准率 或 势 同现 → 优秀封顶（冠胄首词条的
   精准率同样计入组合）；
3. 最大本属攻击 是正常词条，不再被顶级条件排除；
4. 胫甲 keep_wanjia 开关——默认必须有对首领增伤且不得有对玩家增效，
   开关打开后二者有其一即可；
5. 密度型垃圾——次要词条（率/势）堆叠且核心词条不足，且垃圾档先于
   缺陷词条的一般容忍判定。
"""

from lvjiang.apps.yysls.core.equip_parser.models import Affix, EquipmentData
from lvjiang.apps.yysls.core.evaluator import Rating, get_tuning_judge
from tests.case_matrix import case_matrix


def make_equip(equip_type: str, affix_names: list[str],
               quality: str = "gold") -> EquipmentData:
    """构造测试装备（affix_names 第 1 条为首词条）"""
    return EquipmentData(
        type=equip_type,
        name="测试装备",
        level=115,
        quality=quality,
        affixes=[Affix(name=n, value=1.0) for n in affix_names],
    )


def _judge(switches: dict[str, bool] | None = None):
    config = {"switches": switches} if switches else None
    return get_tuning_judge("huixin_modao", config)


# ─── 转律优先级的收录口径 ─────────────────────────────────

def test_transmute_priority_skips_defects_and_secondary_rates():
    """转律不主动去凑 敏/最小外功攻击（缺陷词条）与 会意率/精准率（次优率）。

    这四个词条都留在可用词条库里（出库即判垃圾，拿不到「1 条一般」），
    但不该进转律优先级：敏/小外 出现 1 条就一般封顶，转进来不可能提升评级，
    把它们列进去只会让模拟转律向用户推荐一个永远无效的转律目标。
    """
    priority = _judge().rule.transmute_priority
    assert not {"敏", "最小外功攻击", "会意率", "精准率"} & set(priority)
    assert set(priority) <= set(_judge().rule.affix_pool)


# ─── 缺陷词条：敏 / 最小外功攻击 ───────────────────────────

class TestDefectAffixes:
    @case_matrix("affixes,expected", [
        (["最大外功攻击", "最大外功攻击", "劲", "敏", "会心率"], Rating.NORMAL),
        (["最大外功攻击", "最大外功攻击", "劲", "最小外功攻击", "会心率"], Rating.NORMAL),
        (["最大外功攻击", "最大外功攻击", "劲", "敏", "最小外功攻击"], Rating.JUNK),
        (["最大外功攻击", "最大外功攻击", "劲", "劲", "会心率"], Rating.TOP),
    ])
    def test_qiang_rating(self, affixes, expected):
        assert _judge().judge(make_equip("枪", affixes)).rating == expected


# ─── 会意率的组合封顶 ─────────────────────────────────────

class TestHuiyiCombination:
    @case_matrix("affixes,expected", [
        (["最大外功攻击", "最大外功攻击", "劲", "会意率", "劲"], Rating.TOP),
        (["最大外功攻击", "最大外功攻击", "劲", "会意率", "精准率"],
         Rating.EXCELLENT),
        (["最大外功攻击", "最大外功攻击", "劲", "会意率", "势"],
         Rating.EXCELLENT),
    ])
    def test_qiang_rating(self, affixes, expected):
        assert _judge().judge(make_equip("枪", affixes)).rating == expected

    def test_helm_first_precision_joins_the_pair(self):
        # 冠胄首词条可以是精准率，它照样参与「会意 + 精准」封顶
        e = make_equip("冠胄", ["精准率", "最大外功攻击", "劲", "会意率", "劲"],
                       "purple")
        assert _judge().judge(e).rating == Rating.EXCELLENT


# ─── 最大本属攻击不再排除顶级 ─────────────────────────────

class TestOwnAttrNotExcludedFromTop:
    def test_weapon_wuxiang_top(self):
        # 武器部位的本属即 最大无相攻击
        e = make_equip("枪", ["最大外功攻击", "最大外功攻击", "劲",
                              "最大无相攻击", "劲"])
        assert _judge().judge(e).rating == Rating.TOP

    def test_ring_lieshi_top(self):
        # 非武器部位的本属即玩法属性属攻（威威=裂石）
        e = make_equip("环", ["最大外功攻击", "全武学增效", "最大外功攻击",
                              "劲", "最大裂石攻击"])
        assert _judge().judge(e).rating == Rating.TOP


# ─── 胫甲 keep_wanjia 开关 ────────────────────────────────

class TestJingjiaSwitch:
    @case_matrix("affixes,keep_wanjia,expected", [
        (["劲", "对首领单位增伤", "最大外功攻击", "劲", "会心率"], False, Rating.TOP),
        (["劲", "对玩家单位增效", "最大外功攻击", "劲", "会心率"], False, Rating.JUNK),
        (["劲", "对玩家单位增效", "最大外功攻击", "劲", "会心率"], True, Rating.TOP),
        (["劲", "最大外功攻击", "最大外功攻击", "劲", "会心率"], True, Rating.JUNK),
    ])
    def test_jingjia_rating(self, affixes, keep_wanjia, expected):
        judge = _judge({"keep_wanjia": keep_wanjia})
        assert judge.judge(make_equip("胫甲", affixes, "purple")).rating == expected


# ─── 密度型垃圾（次要词条堆叠 + 核心不足）───────────────────

class TestDensityJunk:
    """次要词条（率/势）堆叠且核心词条不足 → 垃圾。

    各部位的次要集与核心阈值不同：主武器核心 ≤0，其余部位 ≤1；核心集
    按部位含各自的神力词条（环含全武学增效、胫甲含对首领增伤与对玩家增效）。
    """

    # 品阶必须按部位给足：武器与环的品阶门槛是「仅金色」，紫色会先被品阶
    # 短路成垃圾，根本走不到密度条件，用例也就测不到自己声称的契约。
    @case_matrix("equip_type,quality,affixes,expected", [
        # 主武器：次要 3 条、核心 0 条
        ("陌刀", "gold", ["最大外功攻击", "陌刀武学增伤", "势", "势", "会心率"],
         Rating.JUNK),
        # 副武器：次要 3 条（精准/会意/会心）、核心 1 条（大本属）
        ("枪", "gold", ["最大外功攻击", "会心率", "精准率", "会意率",
                        "最大无相攻击"],
         Rating.JUNK),
        # 环：次要 3 条、核心只有全武学增效
        ("环", "gold", ["最大外功攻击", "全武学增效", "会心率", "精准率", "会意率"],
         Rating.JUNK),
        # 胫甲：次要 3 条、核心只有对首领增伤
        ("胫甲", "purple", ["劲", "对首领单位增伤", "会心率", "精准率", "势"],
         Rating.JUNK),
    ])
    def test_density_junk(self, equip_type, quality, affixes, expected):
        assert _judge().judge(
            make_equip(equip_type, affixes, quality)).rating == expected

    def test_core_count_at_threshold_escapes_junk(self):
        # 核心 2 条（势 + 劲，副武器把 势 计入核心）不再触发密度垃圾；
        # 该件只剩「非首缺大外」问题 → 一般
        e = make_equip("枪", ["最大外功攻击", "会心率", "精准率", "势", "劲"])
        assert _judge().judge(e).rating == Rating.NORMAL

    def test_density_junk_outranks_the_defect_tolerance(self):
        """垃圾档先于一般档判定：敏 的「1 条容忍」只在未命中垃圾时生效。

        与上一条只差第 4 个自由槽（劲 → 敏），核心数降到 1 触发密度垃圾，
        因此判垃圾而非一般；否则「加一条缺陷反而从垃圾升到一般」。
        """
        e = make_equip("枪", ["最大外功攻击", "敏", "会心率", "精准率", "势"])
        assert _judge().judge(e).rating == Rating.JUNK
