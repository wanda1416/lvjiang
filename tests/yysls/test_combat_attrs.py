"""战斗属性聚合与动态抗性测试。"""

import pytest

from lvjiang.apps.yysls.core.combat.combat_attrs import (
    CombatAttributes,
    aggregate_equipment_attrs,
    apply_bonus_resistance,
    apply_penetration_resistance,
    apply_three_rate_resistance,
    build_graduation_attrs,
    has_resistance,
)
from lvjiang.apps.yysls.ui.loadout.combat.attrs_tab import CombatAttrsTab


def test_wuxiang_penetration_is_a_fixed_numeric_field() -> None:
    attrs = aggregate_equipment_attrs({
        "head": {"dingyin": {"name": "无相穿透", "value": 14.5}},
    })

    assert attrs.wuxiang_pen == pytest.approx(14.5)
    assert "wuxiang_pen" not in attrs.extra_attrs


@pytest.mark.parametrize("name", [
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
