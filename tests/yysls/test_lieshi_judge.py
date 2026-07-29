"""裂石流派（lieshi_small / lieshi_big）判定测试

覆盖武器规则展开（纯唐/双切/威威 主副武器匹配与择优）、
playstyles 配置过滤、增伤缺失判垃圾、四档条件顺序、
count_max include_first、品阶筛选与调律潜力判定。
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
def big():
    return get_tuning_judge("lieshi_big")


@pytest.fixture
def small():
    return get_tuning_judge("lieshi_small")


# ─── 大外：武器规则展开与择优 ──────────────────────────────

class TestBigWeaponRules:
    def test_modao_shuangqie_main_top(self, big):
        # 陌刀命中 双切/威威 主武器，增伤齐全 → 顶级
        e = make_equip("陌刀", ["最大外功攻击", "陌刀武学增伤", "最大外功攻击", "劲", "敏"])
        assert big.judge(e).rating == Rating.TOP

    def test_modao_no_damage_falls_to_chuntang_sub(self, big):
        # 主武器组合缺增伤判垃圾，但 纯唐 副武器不需增伤 → 择优取顶级
        e = make_equip("陌刀", ["最大外功攻击", "最大外功攻击", "劲", "敏", "会心率"])
        r = big.judge(e)
        assert r.rating == Rating.TOP
        assert any("纯唐 副武器" in s for s in r.reasons)

    def test_modao_only_shuangqie_missing_damage_junk(self, big):
        # 只勾选 双切 时同一装备缺增伤 → 垃圾
        judge = get_tuning_judge("lieshi_big", {"playstyles": ["双切"]})
        e = make_equip("陌刀", ["最大外功攻击", "最大外功攻击", "劲", "敏", "会心率"])
        r = judge.judge(e)
        assert r.rating == Rating.JUNK
        assert any("缺失必需增伤词条" in s for s in r.reasons)

    def test_hengdao_chuntang_main_top_with_label(self, big):
        # 横刀命中 纯唐 主武器（双切副的横刀武学增伤是池外词条 → 垃圾）
        e = make_equip("横刀", ["最大外功攻击", "横刀武学增伤", "最大外功攻击", "劲", "敏"])
        r = big.judge(e)
        assert r.rating == Rating.TOP
        assert any(s.startswith("[纯唐 主武器]") for s in r.reasons)

    def test_qiang_weiwei_sub_top(self, big):
        # 枪只命中 威威 副武器（不需增伤）
        e = make_equip("枪", ["最大外功攻击", "最大外功攻击", "劲", "敏", "会心率"])
        assert big.judge(e).rating == Rating.TOP

    def test_fan_not_applicable(self, big):
        e = make_equip("扇", ["最大外功攻击", "最大外功攻击", "劲", "敏", "会心率"])
        r = big.judge(e)
        assert r.skipped and r.not_applicable


# ─── 大外：四档条件与档序 ──────────────────────────────────

class TestBigTiers:
    def test_main_normal_before_top(self, big):
        # 缺 最大外功攻击 命中一般条件；一般先于顶级判定
        e = make_equip("陌刀", ["最大外功攻击", "陌刀武学增伤", "劲", "劲", "敏"])
        assert big.judge(e).rating == Rating.NORMAL

    def test_main_excellent(self, big):
        # 势 破坏顶级排除条件 → 优秀
        e = make_equip("陌刀", ["最大外功攻击", "陌刀武学增伤", "最大外功攻击", "劲", "势"])
        assert big.judge(e).rating == Rating.EXCELLENT

    def test_ring_missing_quanwuxue_junk(self, big):
        e = make_equip("环", ["最大外功攻击", "最大外功攻击", "劲", "敏", "会心率"])
        assert big.judge(e).rating == Rating.JUNK

    def test_ring_out_of_pool_junk(self, big):
        # 最大鸣金攻击 不在裂石词条库 → 垃圾
        e = make_equip("环", ["最大外功攻击", "全武学增效", "劲", "敏", "最大鸣金攻击"])
        r = big.judge(e)
        assert r.rating == Rating.JUNK
        assert any("垃圾词条" in s for s in r.reasons)

    def test_ring_top(self, big):
        e = make_equip("环", ["最大外功攻击", "全武学增效", "最大外功攻击", "劲", "敏"])
        assert big.judge(e).rating == Rating.TOP

    def test_helm_one_zhunji_top(self, big):
        # count_max include_first：首会心 + 1 精准（计 1 ≤ 1）→ 顶级
        e = make_equip("冠胄", ["会心率", "精准率", "最大外功攻击", "劲", "敏"],
                       quality="purple")
        assert big.judge(e).rating == Rating.TOP

    def test_helm_double_zhunji_excellent(self, big):
        # 首精准 + 1 精准（含首计 2 > 1）→ 顶级条件不成立 → 优秀
        e = make_equip("冠胄", ["精准率", "精准率", "最大外功攻击", "劲", "敏"],
                       quality="purple")
        assert big.judge(e).rating == Rating.EXCELLENT

    def test_leg_top(self, big):
        e = make_equip("胫甲", ["劲", "对首领单位增伤", "最大外功攻击", "劲", "敏"],
                       quality="purple")
        assert big.judge(e).rating == Rating.TOP

    def test_leg_missing_boss_damage_junk(self, big):
        e = make_equip("胫甲", ["劲", "最大外功攻击", "最大外功攻击", "劲", "敏"],
                       quality="purple")
        assert big.judge(e).rating == Rating.JUNK


# ─── 小外：独立池与部位差异 ────────────────────────────────

class TestSmall:
    def test_main_top(self, small):
        e = make_equip("陌刀", ["最小外功攻击", "陌刀武学增伤", "最小外功攻击", "敏", "最大无相攻击"])
        assert small.judge(e).rating == Rating.TOP

    def test_jin_out_of_pool_junk(self, small):
        # 劲 不在小外词条库（大外池才有）→ 垃圾
        e = make_equip("陌刀", ["最小外功攻击", "陌刀武学增伤", "劲", "敏", "最大无相攻击"])
        assert small.judge(e).rating == Rating.JUNK

    def test_leg_first_is_huixin(self, small):
        # 小外胫甲首词条为 会心率/精准率（与大外的 劲 不同）
        e = make_equip("胫甲", ["会心率", "对首领单位增伤", "最小外功攻击", "敏", "最大无相攻击"],
                       quality="purple")
        assert small.judge(e).rating == Rating.TOP

    def test_leg_jin_first_skipped(self, small):
        e = make_equip("胫甲", ["劲", "对首领单位增伤", "最小外功攻击", "敏", "最大无相攻击"],
                       quality="purple")
        assert small.judge(e).skipped

    def test_leg_huixin_token_excellent(self, small):
        # 非首词条再出现会心 → 顶级排除条件不成立 → 优秀
        e = make_equip("胫甲", ["会心率", "对首领单位增伤", "最小外功攻击", "敏", "会心率"],
                       quality="purple")
        assert small.judge(e).rating == Rating.EXCELLENT


# ─── 品阶与首词条筛选 ──────────────────────────────────────

class TestFilters:
    def test_purple_weapon_skipped(self, big):
        e = make_equip("陌刀", ["最大外功攻击", "陌刀武学增伤", "最大外功攻击", "劲", "敏"],
                       quality="purple")
        assert big.judge(e).skipped

    def test_wrong_first_skipped(self, big):
        e = make_equip("陌刀", ["劲", "陌刀武学增伤", "最大外功攻击", "劲", "敏"])
        assert big.judge(e).skipped


# ─── 调律潜力判定 ──────────────────────────────────────────

class TestPotential:
    def test_ring_transmute_waste_top(self, big):
        # 池外词条可被转律洗掉，剩余空槽按价值序补齐 → 仍可达顶级
        # （全武学增效在 lieshi 价值序靠后不会被填充，需装备自带
        # 才能逃离环垃圾条件 count_max[全武学增效]）
        e = make_equip("环", ["最大外功攻击", "最大鸣金攻击", "全武学增效"])
        assert big.check_tuning_worthiness(e).rating == Rating.TOP

    def test_ring_double_waste_junk(self, big):
        # 两条池外词条，仅可转律一条 → 垃圾
        e = make_equip("环", ["最大外功攻击", "最大鸣金攻击", "最大牵丝攻击"])
        assert big.check_tuning_worthiness(e).rating == Rating.JUNK

    def test_weapon_full_missing_damage_junk(self, big):
        # 词条已满且缺增伤：转律不产生神力词条 → 垃圾
        judge = get_tuning_judge("lieshi_big", {"playstyles": ["双切"]})
        e = make_equip("陌刀", ["最大外功攻击", "最大外功攻击", "劲", "敏", "会心率"])
        assert judge.check_tuning_worthiness(e).rating == Rating.JUNK

    def test_weapon_free_slot_fills_damage_top(self, big):
        # 有空槽可补增伤 → 仍可达顶级
        judge = get_tuning_judge("lieshi_big", {"playstyles": ["双切"]})
        e = make_equip("陌刀", ["最大外功攻击", "最大外功攻击", "劲", "敏"])
        assert judge.check_tuning_worthiness(e).rating == Rating.TOP
