"""战斗属性聚合与动态抗性测试。"""

import pytest

from lvjiang.apps.yysls.core.combat.combat_attrs import (
    aggregate_equipment_attrs,
    apply_bonus_resistance,
    apply_penetration_resistance,
    apply_three_rate_resistance,
)
from lvjiang.apps.yysls.ui.combat_attrs_tab import CombatAttrsTab


def test_wuxiang_penetration_is_a_fixed_numeric_field() -> None:
    attrs = aggregate_equipment_attrs({
        "head": {"dingyin": {"name": "无相穿透", "value": 14.5}},
    })

    assert attrs.wuxiang_pen == pytest.approx(14.5)
    assert "wuxiang_pen" not in attrs.extra_attrs


def test_resistance_functions_accept_level_config_values() -> None:
    assert apply_three_rate_resistance("crit_rate", 1.0, 100) == pytest.approx(0.5)
    assert apply_bonus_resistance(0.3, resistance=20) == pytest.approx(0.25)
    assert apply_penetration_resistance(12, 36, 20) == pytest.approx(46)


def test_combat_panel_uses_highest_level_resistances() -> None:
    assert CombatAttrsTab._current_resistances() == (145.0, 15.0)
