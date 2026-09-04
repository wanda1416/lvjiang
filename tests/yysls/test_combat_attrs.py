"""战斗属性聚合与动态抗性测试。"""

import pytest

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.core.combat.combat_attrs import (
    CombatAttributes,
    aggregate_equipment_attrs,
    apply_bonus_resistance,
    apply_penetration_resistance,
    apply_three_rate_resistance,
    build_graduation_attrs,
    convert_five_dims,
    has_resistance,
)
from lvjiang.apps.yysls.ui.loadout.combat.attrs_tab import CombatAttrsTab
from tests.case_matrix import case_matrix


def test_wuxiang_penetration_is_a_fixed_numeric_field() -> None:
    attrs = aggregate_equipment_attrs({
        "head": {"dingyin": {"name": "无相穿透", "value": 14.5}},
    })

    assert attrs.wuxiang_pen == pytest.approx(14.5)
    assert "wuxiang_pen" not in attrs.extra_attrs


@case_matrix("name", [
    "十方破阵武学技增伤",
    "千机索天重击增伤",
    "明川药典治疗技增疗",
])
def test_configured_skill_bonus_is_aggregated_as_dynamic_bonus(name: str) -> None:
    attrs = aggregate_equipment_attrs({
        "head": {"dingyin": {"name": name, "value": 8.0}},
    })

    assert attrs.extra_attrs[name] == pytest.approx(0.08)
    assert has_resistance(name)


def test_unconfigured_skill_like_name_is_not_accepted_by_suffix() -> None:
    name = "不存在的武学技增伤"
    attrs = aggregate_equipment_attrs({
        "head": {"dingyin": {"name": name, "value": 8.0}},
    })

    assert name not in attrs.extra_attrs
    assert not has_resistance(name)


def test_resistance_functions_accept_level_config_values() -> None:
    assert apply_three_rate_resistance("crit_rate", 1.0, 100) == pytest.approx(0.5)
    assert apply_bonus_resistance(0.3, resistance=20) == pytest.approx(0.25)
    assert apply_penetration_resistance(12, 36, 20) == pytest.approx(46)


def test_combat_panel_uses_highest_level_resistances() -> None:
    assert CombatAttrsTab._current_resistances() == (145.0, 15.0)


def test_build_graduation_attrs_is_the_shared_resistance_boundary() -> None:
    base = CombatAttributes(
        precision=0.8, outer_pen=36, lieshi_pen=36, boss_bonus=0.08,
    )
    equipment = CombatAttributes(
        precision=0.2, outer_pen=12, wuxiang_pen=14.5, boss_bonus=0.015,
    )
    result = build_graduation_attrs(base, equipment, "裂石·钧")

    assert result.precision == pytest.approx(
        apply_three_rate_resistance("precision", 1.0, 145))
    assert result.outer_pen == pytest.approx(
        apply_penetration_resistance(12, 36, 15))
    assert result.lieshi_pen == pytest.approx(
        apply_penetration_resistance(14.5, 36, 15))
    assert result.boss_bonus == pytest.approx(
        apply_bonus_resistance(0.095, resistance=15))


# ── 五维转换 ──────────────────────────────────────────────

#: 归一容差。当前实测系数下最差 0.986（势/敏），留 2% 余量；
#: 三率侧那 1.4% 缺口查清后应收紧。
_FIVE_DIM_TOLERANCE = 0.02

_DIM_ARG = {"劲": "jin", "势": "shi", "敏": "min_val"}


def _cap(level: int, category: str) -> float:
    entry = get_game_config().get_affix_caps(level, category)
    assert entry is not None, f"affix_caps 缺少 {level} 级的 {category}"
    return float(entry["cap"])


@case_matrix("dimension", ["劲", "势", "敏"])
def test_one_full_dimension_affix_is_worth_exactly_one_affix(dimension: str) -> None:
    """一条满值五维词条产出的各项，按各自词条满值归一后相加应为 1。

    这是判断转换系数对不对的硬标准。早期那组自行拟合的系数
    （敏 1.0小外攻 + 0.075%会心）归一后是 1.044——超过一整条词条，
    不可能成立，正是靠这条不变量认出来的。

    系数与 affix_caps 任一侧改动都会打破它，所以两边漂移都会在这里红灯。
    """
    level = 110
    attrs = convert_five_dims(**{_DIM_ARG[dimension]: _cap(level, "五维属性")})

    normalized = (attrs.min_outer + attrs.max_outer) / _cap(level, "外功攻击")
    normalized += attrs.crit_rate * 100 / _cap(level, "会心率")
    normalized += attrs.intent_rate * 100 / _cap(level, "会意率")

    assert normalized == pytest.approx(1.0, abs=_FIVE_DIM_TOLERANCE), (
        f"{dimension} 归一后为 {normalized:.4f}，偏离一整条词条超过 "
        f"{_FIVE_DIM_TOLERANCE:.0%}；系数或 affix_caps 有一侧不对"
    )


def test_five_dimension_conversion_targets() -> None:
    """每一维只落到它该落的字段上，不串味。"""
    jin = convert_five_dims(jin=100)
    assert (jin.min_outer, jin.max_outer) == pytest.approx((22.5, 136.0))
    assert (jin.crit_rate, jin.intent_rate) == (0.0, 0.0)

    shi = convert_five_dims(shi=100)
    assert (shi.max_outer, shi.intent_rate) == pytest.approx((90.0, 0.038))
    assert (shi.min_outer, shi.crit_rate) == (0.0, 0.0)

    agility = convert_five_dims(min_val=100)
    assert (agility.min_outer, agility.crit_rate) == pytest.approx((90.0, 0.076))
    assert (agility.max_outer, agility.intent_rate) == (0.0, 0.0)


def test_body_and_defence_produce_nothing_trackable() -> None:
    """体/御 只出生命值与防御，CombatAttributes 不追踪，必须是全零。"""
    assert convert_five_dims(ti=100, yu=100) == CombatAttributes()


def test_five_dimension_affixes_are_summed_before_conversion() -> None:
    """多件装备上的同一维先累计再换算，避免逐件取整放大误差。"""
    split = aggregate_equipment_attrs({
        "head": {"affix_1": {"name": "敏", "value": 40.0}},
        "chest": {"affix_1": {"name": "敏", "value": 36.8}},
    })

    assert split.min_outer == pytest.approx(convert_five_dims(min_val=76.8).min_outer)
    assert split.crit_rate == pytest.approx(convert_five_dims(min_val=76.8).crit_rate)


def test_five_dimension_affixes_reach_the_aggregate() -> None:
    """五维词条要真的进聚合结果——它经反推路径决定基础属性，
    静默丢弃会让基础属性整体偏高。"""
    attrs = aggregate_equipment_attrs({
        "head": {"affix_1": {"name": "劲", "value": 76.8}},
    })

    assert attrs.min_outer > 0 and attrs.max_outer > 0
