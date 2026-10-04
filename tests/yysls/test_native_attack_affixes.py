"""无相→本属的常驻业务契约。"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.core.combat.affix_rules import normal_affix_candidates
from lvjiang.apps.yysls.core.combat.combat_attrs import aggregate_equipment_attrs
from lvjiang.apps.yysls.core.equip_parser.models import Affix, EquipmentData
from lvjiang.apps.yysls.core.equip_validator import validate_combination_dict
from lvjiang.apps.yysls.core.evaluator.rule_judge import GenericTuningJudge
from lvjiang.apps.yysls.core.loadout.transmute import (
    TARGET_NAME_KEY,
    TARGET_VALUE_KEY,
    transmute_pool_union,
    transmute_target_value,
    transmute_targets,
    validate_saved_target,
    with_transmuted_affix,
)
from lvjiang.apps.yysls.core.tuning_rules import dynamic_affix_map, parse_tuning_rule
from lvjiang.apps.yysls.core.tuning_rules.models import Condition, expand_affix_names

ROOT = Path(__file__).parents[2]
RULES = ROOT / "config/system/yysls/tuning_rules"


def rating_code(result):
    return "S" if result.skipped else result.rating.value[0].upper()


@pytest.mark.parametrize("attr", ["鸣金", "裂石", "牵丝", "破竹"])
def test_weapon_identity_has_no_foreign_attack(attr):
    aliases = dynamic_affix_map(attr, weapon=True)
    assert aliases == {"最大无相攻击": "最大本属攻击", "最小无相攻击": "最小本属攻击"}
    gc = get_game_config()
    for level in (110, 115):
        physical = normal_affix_candidates({"type": "剑", "level": level}, gc)
        assert expand_affix_names(["最大本属攻击"], physical, aliases) == ["最大无相攻击"]
        assert expand_affix_names(["最大外属攻击", "最小外属攻击"], physical, aliases) == []
    condition = Condition(kind="count_min", symbols=["最大无相攻击", "最大本属攻击"], min=2)
    assert not condition.check("劲", ["最大无相攻击"], aliases)
    assert condition.check("最大无相攻击", ["最大无相攻击"], aliases) is False
    condition.include_first = True
    assert condition.check("最大无相攻击", ["最大无相攻击"], aliases)


def test_school_pools_expand_in_owner_context_without_mutating_config():
    gc = copy.copy(get_game_config())
    gc._schools = {
        "测试甲": {"attr": "鸣金", "transmute_pool": ["最大本属攻击", "劲"]},
        "测试乙": {"attr": "裂石", "transmute_pool": ["劲", "最大本属攻击"]},
    }
    original = copy.deepcopy(gc._schools)
    assert gc.get_transmute_pool("测试甲") == ["最大无相攻击", "最大鸣金攻击", "劲"]
    assert gc.get_transmute_pool("测试乙") == ["劲", "最大无相攻击", "最大裂石攻击"]
    assert transmute_pool_union(gc) == ["最大无相攻击", "最大鸣金攻击", "劲", "最大裂石攻击"]
    assert gc._schools == original


@pytest.mark.parametrize("level", [110, 115])
@pytest.mark.parametrize("chengyin", [False, True])
def test_real_transmute_values_saved_targets_and_graduation(level, chengyin):
    from lvjiang.apps.yysls.core.graduation import (
        get_graduation_calculator,
        get_graduation_scheme_combat_attrs,
    )

    gc = get_game_config()
    for school in gc.get_schools():
        attr = gc.get_school_attr(school)
        aliases = dynamic_affix_map(attr)
        for kind, slot, literal in [
            ("剑", "main_weapon", "最大无相攻击"),
            ("环", "ring", f"最大{attr}攻击"),
        ]:
            equipment = {
                "type": kind, "name": "测试装备", "quality": "gold", "level": level,
                "original_level": 110, "is_chengyin": chengyin,
                "affix_1": {"name": "最大外功攻击", "value": 100},
                "affix_2": {"name": "会心率", "value": 5, "unit": "%"},
                "affix_3": {"name": "劲", "value": 60},
            }
            original = copy.deepcopy(equipment)
            physical = normal_affix_candidates(equipment, gc)
            expanded = expand_affix_names(["最大本属攻击"], physical, aliases)
            assert expanded == [literal]
            assert transmute_targets(equipment, 2, expanded, gc) == [literal]
            cap = transmute_target_value(literal, level, chengyin, gc)
            assert cap == gc.get_affix_caps(level, literal)["chengyin" if chengyin else "cap"]
            dynamic_result = with_transmuted_affix(equipment, 2, expanded[0], cap, gc)
            literal_result = with_transmuted_affix(equipment, 2, literal, cap, gc)
            assert dynamic_result == literal_result
            assert equipment == original
            # 已保存目标仍是游戏真实名称，不因词条库精简变成非法目标。
            saved = copy.deepcopy(equipment)
            saved["affix_2"].update({TARGET_NAME_KEY: literal, TARGET_VALUE_KEY: cap})
            assert validate_saved_target(saved, gc) is None
            actual_attrs = aggregate_equipment_attrs({slot: dynamic_result})
            expected_attrs = aggregate_equipment_attrs({slot: literal_result})
            assert actual_attrs == expected_attrs
            if kind == "剑":
                assert actual_attrs.max_wuxiang == cap
            calculator = get_graduation_calculator(school, world_level=level)
            assert calculator is not None
            base = get_graduation_scheme_combat_attrs(school, "基础方案", level)
            assert calculator.calculate(base + actual_attrs) == calculator.calculate(base + expected_attrs)


def test_dynamic_first_affix_and_weapon_potential():
    raw = {
        "key": "native", "name": "本属测试", "playstyles": ["纯唐"],
        "affix_pool": ["最大本属攻击", "最小本属攻击", "劲", "敏", "会心率"],
        "transmute_priority": ["最大本属攻击"],
        "patterns": {"副武器": {
            "first": ["最大本属攻击"],
            "top_conditions": [{"contains_all": ["最大本属攻击", "劲"]}],
        }},
    }
    judge = GenericTuningJudge(parse_tuning_rule(raw))
    equip = EquipmentData(
        type="陌刀", name="测试装备", level=110, quality="gold",
        affixes=[Affix(name=n, value=1) for n in ["最大无相攻击", "劲"]],
    )
    result = judge.check_tuning_worthiness(equip)
    assert rating_code(result) == "顶"
    assert "最大无相攻击" in " ".join(result.reasons)
    assert "最大本属攻击" not in " ".join(result.reasons).split("：")[0]


def test_weapon_does_not_treat_specific_element_as_native():
    raw = yaml.safe_load((RULES / "heal_fire.yaml").read_text(encoding="utf-8"))
    judge = GenericTuningJudge(parse_tuning_rule(raw))
    equip = EquipmentData(
        type="扇", name="测试装备", level=110, quality="gold",
        affixes=[Affix(name=n, value=1) for n in [
            "最大外功攻击", "最大外功攻击", "劲", "最小外功攻击", "最大牵丝攻击",
        ]],
    )
    assert validate_combination_dict(equip.to_dict(include_fp=False))
    assert rating_code(judge.judge(equip)) == "垃"
    equip.affixes[-1].name = "最大无相攻击"
    assert not validate_combination_dict(equip.to_dict(include_fp=False))
    assert rating_code(judge.judge(equip)) == "优"


def test_school_editor_uses_draft_attr_and_saves_dynamic_names(qtbot, monkeypatch):
    from lvjiang.apps.yysls.ui.game_settings import school_panel

    data = {"schools": {"测试流派": {"attr": "牵丝", "transmute_pool": ["劲"]}}}
    changes = []
    panel = school_panel.SchoolPanel(data=data, on_changed=lambda: changes.append(True))
    qtbot.addWidget(panel)
    panel._refresh_list(select="测试流派")
    assert changes == []

    class Dialog:
        def __init__(self, candidates, selected, *args):
            assert "最大本属攻击" in candidates
            assert "最大无相攻击" not in candidates
            assert "最大牵丝攻击" not in candidates
            assert selected == ["劲"]

        def exec(self):
            return 1

        def selected(self):
            return ["劲", "最大本属攻击"]

    monkeypatch.setattr(school_panel, "AffixSelectSortDialog", Dialog)
    panel._on_pool_edit()
    assert data["schools"]["测试流派"]["transmute_pool"] == ["劲", "最大本属攻击"]
    assert len(changes) == 1
