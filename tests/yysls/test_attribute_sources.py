"""来源拆分必须与真实公共计算链路一致，且不改写装备或基础属性。"""

from copy import deepcopy

import pytest

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.core.combat.attribute_sources import (
    build_attribute_source_report,
)
from lvjiang.apps.yysls.core.combat.combat_attrs import (
    CombatAttributes,
    GraduationAttrContext,
    fold_wuxiang_pen,
)
from lvjiang.apps.yysls.core.graduation.assumptions import Assumptions
from lvjiang.apps.yysls.core.graduation.scoring import equipment_attrs, graduation_input


def test_sources_match_scoring_with_stacking_dimensions_sets_and_penetration():
    config = get_game_config()
    base = CombatAttributes(min_outer=100, precision=1.134, mingjin_pen=6)
    gongjue = CombatAttributes(intent_rate=0.04)
    equipped = {
        "main_weapon": {
            "level": 115, "quality": "gold", "equipment_set": "yudou",
            "affix_1": {"name": "劲", "value": 30},
            "affix_2": {"name": "剑武学增伤", "value": 5},
            "dingyin": {"name": "无相穿透", "value": 10},
        },
        "sub_weapon": {
            "level": 110, "quality": "gold", "equipment_set": "yudou",
            "affix_1": {"name": "剑武学增伤", "value": 8},
        },
        "ring": {"level": 115, "quality": "gold", "equipment_set": "yudou"},
        "pendant": {"level": 115, "quality": "gold", "equipment_set": "yudou"},
        "head": {"dingyin": {"name": "无名剑法武学技增伤", "value": 8}},
    }
    original = deepcopy(equipped)
    context = GraduationAttrContext.from_school("鸣金·虹", world_level=115, game_config=config)
    projected = Assumptions(full_level=115).project(equipped)
    report = build_attribute_source_report(
        base, gongjue, projected, context=context, school="鸣金·虹", game_config=config)
    expected_raw = base + gongjue + fold_wuxiang_pen(equipment_attrs(projected, config), context.target_pen_field)
    expected_final = graduation_input(base + gongjue, projected, "鸣金·虹", config, attr_context=context)
    summed = CombatAttributes()
    for contribution in report.contributions:
        summed = summed + contribution.attrs
    for attrs in (summed, report.raw):
        for field, value in expected_raw.to_dict().items():
            if field == "extra_attrs":
                assert attrs.extra_attrs == pytest.approx(value)
            else:
                assert getattr(attrs, field) == pytest.approx(value)
    assert report.effective.to_dict() == expected_final.to_dict()
    assert report.category_attrs("equipment_base").min_outer == 380
    assert report.category_attrs("equipment_set").max_outer > 0
    assert report.category_attrs("affix").extra_attrs["剑武学增伤"] == pytest.approx(0.08)
    assert report.category_attrs("dingyin").mingjin_pen == 10
    assert report.category_attrs("dingyin").wuxiang_pen == 0
    assert report.excluded == ("主武器 · 剑武学增伤",)
    assert any("五维转换" in source.label for source in report.contributions)
    assert equipped == original
    base.min_outer = 999
    assert report.category_attrs("base").min_outer == 100
