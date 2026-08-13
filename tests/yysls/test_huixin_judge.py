"""会心流派（huixin_small / huixin_big）判定测试

覆盖武器规则展开（纯唐/双切/威威 主副武器匹配与择优）、
playstyles 配置过滤、增伤缺失判垃圾、四档条件顺序、
count_max include_first、品阶筛选与调律潜力判定。
"""

import pytest

from lvjiang.apps.yysls.equip_parser.models import Affix, EquipmentData
from lvjiang.apps.yysls.evaluator import Rating, get_tuning_judge


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
    return get_tuning_judge("huixin_big")


@pytest.fixture
def small():
    return get_tuning_judge("huixin_small")


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
        judge = get_tuning_judge("huixin_big", {"playstyles": ["双切"]})
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

    def test_fan_zoudiyu_sub_top(self, big):
        # 扇命中 走地玉 副武器（不需增伤），同 威威 副武器写法
        e = make_equip("扇", ["最大外功攻击", "最大外功攻击", "劲", "敏", "会心率"])
        assert big.judge(e).rating == Rating.TOP

    def test_unlisted_weapon_not_applicable(self, big):
        # 剑不在任何玩法的主/副武器内 → 不适用
        e = make_equip("剑", ["最大外功攻击", "最大外功攻击", "劲", "敏", "会心率"])
        r = big.judge(e)
        assert r.skipped and r.not_applicable


# ─── 大外：四档条件与档序 ──────────────────────────────────

class TestBigTiers:
    def test_main_normal_before_top(self, big):
        # 势 为缺陷词条命中一般条件；一般先于顶级判定
        e = make_equip("陌刀", ["最大外功攻击", "陌刀武学增伤", "最大外功攻击", "劲", "势"])
        assert big.judge(e).rating == Rating.NORMAL

    def test_main_defect_pair_normal(self, big):
        # 精准+会心（非首）同现 为缺陷 → 一般
        e = make_equip("陌刀", ["最大外功攻击", "陌刀武学增伤", "最大外功攻击", "会心率", "精准率"])
        assert big.judge(e).rating == Rating.NORMAL

    def test_main_excellent(self, big):
        # 单会心非缺陷，但破坏主武器顶级排除条件 → 优秀
        e = make_equip("陌刀", ["最大外功攻击", "陌刀武学增伤", "最大外功攻击", "劲", "会心率"])
        assert big.judge(e).rating == Rating.EXCELLENT

    def test_main_missing_core_excellent(self, big):
        # 缺非首大外不算缺陷（可调出）→ 优秀
        e = make_equip("陌刀", ["最大外功攻击", "陌刀武学增伤", "劲", "劲", "敏"])
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
        # （全武学增效在 huixin 价值序靠后不会被填充，需装备自带
        # 才能逃离环垃圾条件 count_max[全武学增效]）
        e = make_equip("环", ["最大外功攻击", "最大鸣金攻击", "全武学增效"])
        assert big.check_tuning_worthiness(e).rating == Rating.TOP

    def test_ring_double_waste_junk(self, big):
        # 两条池外词条，仅可转律一条 → 垃圾（限定裂石玩法：
        # 全选时 走地玉（牵丝）会把 最大牵丝攻击 归类为池内本属）
        judge = get_tuning_judge("huixin_big", {"playstyles": ["双切"]})
        e = make_equip("环", ["最大外功攻击", "最大鸣金攻击", "最大牵丝攻击"])
        assert judge.check_tuning_worthiness(e).rating == Rating.JUNK

    def test_weapon_full_missing_damage_junk(self, big):
        # 词条已满且缺增伤：转律不产生神力词条 → 垃圾
        judge = get_tuning_judge("huixin_big", {"playstyles": ["双切"]})
        e = make_equip("陌刀", ["最大外功攻击", "最大外功攻击", "劲", "敏", "会心率"])
        assert judge.check_tuning_worthiness(e).rating == Rating.JUNK

    def test_weapon_free_slot_fills_damage_top(self, big):
        # 有空槽可补增伤 → 仍可达顶级
        judge = get_tuning_judge("huixin_big", {"playstyles": ["双切"]})
        e = make_equip("陌刀", ["最大外功攻击", "最大外功攻击", "劲", "敏"])
        assert judge.check_tuning_worthiness(e).rating == Rating.TOP


# ─── 小外：动态属攻词条归类（本属/外属）──────────────────

class TestSmallDynamic:
    """非武器部位具体属攻双重身份参与动态词条匹配：
    attr=裂石 时 最小裂石→最小本属攻击、其余最小属攻→最小
    外属攻击、最大异属→最大外属攻击（池外）；规则级 common
    条件：小属攻共 2 条判垃圾、1 条判一般。"""

    @pytest.fixture
    def small_lieshi(self):
        # 限定裂石玩法，固定动态词条视角（避开破竹鸢择优）
        return get_tuning_judge("huixin_small", {"playstyles": ["双切"]})

    def test_ring_no_small_attr_top(self, small_lieshi):
        # 基线：无小属攻且无三率 → 顶级
        e = make_equip("环", ["最小外功攻击", "全武学增效", "最小外功攻击", "敏", "敏"])
        assert small_lieshi.judge(e).rating == Rating.TOP

    def test_ring_huixin_excellent(self, small_lieshi):
        # 有神力部位非首会心 → 优秀封顶
        e = make_equip("环", ["最小外功攻击", "全武学增效", "最小外功攻击", "敏", "会心率"])
        assert small_lieshi.judge(e).rating == Rating.EXCELLENT

    def test_ring_one_foreign_small_attr_normal(self, small_lieshi):
        # 1 条 最小破竹攻击（→最小外属攻击）→ 一般
        e = make_equip("环", ["最小外功攻击", "全武学增效", "最小外功攻击", "最小破竹攻击", "会心率"])
        assert small_lieshi.judge(e).rating == Rating.NORMAL

    def test_ring_one_own_small_attr_normal(self, small_lieshi):
        # 1 条 最小裂石攻击（→最小本属攻击）→ 一般
        e = make_equip("环", ["最小外功攻击", "全武学增效", "最小外功攻击", "最小裂石攻击", "会心率"])
        assert small_lieshi.judge(e).rating == Rating.NORMAL

    def test_ring_two_small_attrs_junk(self, small_lieshi):
        # 最小本属 + 最小外属 共 2 条 → 垃圾
        e = make_equip("环", ["最小外功攻击", "全武学增效", "最小裂石攻击", "最小破竹攻击", "会心率"])
        assert small_lieshi.judge(e).rating == Rating.JUNK

    def test_ring_big_foreign_attr_junk(self, small_lieshi):
        # 最大牵丝攻击（→最大外属攻击，池外）→ 垃圾
        e = make_equip("环", ["最小外功攻击", "全武学增效", "最小外功攻击", "最大牵丝攻击", "会心率"])
        r = small_lieshi.judge(e)
        assert r.rating == Rating.JUNK
        assert any("垃圾词条" in s for s in r.reasons)

    def test_weapon_literal_wuxiang_unaffected(self, small_lieshi):
        # 武器部位不做归类：字面无相保持原判定（属攻不掉武器，
        # common 小属攻计数恒 0 无害）
        e = make_equip("陌刀", ["最小外功攻击", "陌刀武学增伤", "最小外功攻击", "敏", "最大无相攻击"])
        assert small_lieshi.judge(e).rating == Rating.TOP


# ─── 单一流派：真实属攻词条字面引用 ──────────────────────

class TestLiteralSpecificAttr:
    """双重身份匹配：单一流派（attr=裂石）规则既可字面引用
    真实属攻词条，也可用动态词条，同一件装备均能命中。"""

    @staticmethod
    def _make_judge(pool_symbol: str):
        from lvjiang.apps.yysls.evaluator.judge import GenericTuningJudge
        from lvjiang.apps.yysls.evaluator.tuning_rules import parse_tuning_rule
        data = {
            "key": "t1",
            "name": "字面属攻规则",
            "playstyles": {"裂石流": {
                "main": {"weapon": "剑", "damage": None},
                "sub": {"weapon": "枪", "damage": None},
                "attr": "裂石"}},
            "affix_pool": ["最大外功攻击", pool_symbol,
                           "劲", "敏", "会心率"],
            "patterns": {"环": {
                "first": ["最大外功攻击"],
                "default_rating": "junk",
                "top_conditions": [{"contains_all": [pool_symbol]}],
            }},
        }
        return GenericTuningJudge(parse_tuning_rule(data))

    def test_literal_specific_hit(self):
        # 规则字面引用 最大裂石攻击 → 装备字面身份命中 → 顶级
        judge = self._make_judge("最大裂石攻击")
        e = make_equip("环", ["最大外功攻击", "最大裂石攻击", "劲", "敏", "会心率"])
        assert judge.judge(e).rating == Rating.TOP

    def test_dynamic_alias_hits_same_equip(self):
        # 规则改写 最大本属攻击 → 同一装备经动态归类身份命中 → 顶级
        judge = self._make_judge("最大本属攻击")
        e = make_equip("环", ["最大外功攻击", "最大裂石攻击", "劲", "敏", "会心率"])
        assert judge.judge(e).rating == Rating.TOP

    def test_foreign_attr_misses_literal(self):
        # 装备是 最大破竹攻击：字面不等于裂石、动态身份为
        # 最大外属攻击，两重身份均不在池 → 池外判垃圾
        judge = self._make_judge("最大裂石攻击")
        e = make_equip("环", ["最大外功攻击", "最大破竹攻击", "劲", "敏", "会心率"])
        assert judge.judge(e).rating == Rating.JUNK
