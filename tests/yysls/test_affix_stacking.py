"""词组同名叠加方式（_stack）与部位合法性在所有毕业率入口的一致性。"""
from __future__ import annotations

import copy
from types import SimpleNamespace

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.config.constants import STACK_MAX, STACK_SUM
from lvjiang.apps.yysls.core.combat.combat_attrs import (
    CombatAttributes,
    aggregate_equipment_attrs,
    build_graduation_attrs,
    compute_equip_base_attrs,
    effective_equipped,
    max_stack_affixes,
)
from lvjiang.apps.yysls.core.graduation.optimal_combo import search_optimal_combo


def _sword(name: str, bonus: float, *, extra: dict | None = None) -> dict:
    equip = {
        "name": name, "type": "剑", "level": 110, "quality": "gold",
        "affix_1": {"name": "最大外功攻击", "value": 100},
        "affix_2": {"name": "剑武学增伤", "value": bonus, "unit": "%"},
    }
    if extra:
        equip["affix_3"] = extra
    return equip


def test_config_exposes_stack_mode_with_sum_default():
    gc = get_game_config()
    assert gc.get_affix_stack("剑武学增伤") == STACK_MAX
    assert gc.get_affix_stack("扇武学增效") == STACK_MAX
    assert gc.get_affix_stack("全武学增效") == STACK_SUM
    assert gc.get_affix_stack("会心率") == STACK_SUM
    assert gc.get_affix_stack("不存在的词条") == STACK_SUM


def test_two_swords_keep_only_the_highest_weapon_bonus():
    equipped = {
        "main_weapon": _sword("主", 9.0),
        "sub_weapon": _sword("副", 8.0),
    }
    effective = effective_equipped(equipped)
    assert effective["main_weapon"]["affix_2"]["name"] == "剑武学增伤"
    assert "affix_2" not in effective["sub_weapon"]
    attrs = aggregate_equipment_attrs(equipped)
    assert abs(attrs.extra_attrs["剑武学增伤"] - 0.09) < 1e-9
    raw = aggregate_equipment_attrs(equipped, normalize=False)
    assert abs(raw.extra_attrs["剑武学增伤"] - 0.17) < 1e-9
    # 原装备未被改写
    assert equipped["sub_weapon"]["affix_2"]["value"] == 8.0


def test_sum_groups_still_add_up_across_parts():
    equipped = {
        "ring": {"type": "环", "level": 110,
                 "affix_1": {"name": "全武学增效", "value": 4.9}},
        "pendant": {"type": "佩", "level": 110,
                    "affix_1": {"name": "全武学增效", "value": 4.0}},
    }
    attrs = aggregate_equipment_attrs(equipped)
    assert abs(attrs.all_skill_bonus - 0.089) < 1e-9


def test_affixes_illegal_for_the_part_or_weapon_do_not_count():
    equipped = {
        "sub_weapon": _sword("枪上带剑增", 9.0),
        "ring": {"type": "环", "level": 110,
                 "affix_1": {"name": "全武学增效", "value": 4.9},
                 "affix_2": {"name": "剑武学增伤", "value": 9.8}},
    }
    equipped["sub_weapon"]["type"] = "枪"
    effective = effective_equipped(equipped)
    assert "affix_2" not in effective["sub_weapon"]
    assert "affix_2" not in effective["ring"]
    assert "剑武学增伤" not in aggregate_equipment_attrs(equipped).extra_attrs


def test_untyped_hypothetical_equipment_is_not_filtered():
    attrs = aggregate_equipment_attrs({
        "hypothetical": {"affix_1": {"name": "剑武学增伤", "value": 9.8}},
    })
    assert abs(attrs.extra_attrs["剑武学增伤"] - 0.098) < 1e-9


def test_max_stack_affixes_reports_only_max_groups():
    equip = _sword("主", 9.0, extra={"name": "会心率", "value": 5.0})
    assert max_stack_affixes(equip) == {"剑武学增伤": 9.0}


def test_stack_mode_follows_config_not_hardcoded_names():
    gc = get_game_config()

    class _Proxy:
        def get_affix_stack(self, name):
            return STACK_MAX if name == "会心率" else STACK_SUM

        def __getattr__(self, item):
            return getattr(gc, item)

    equipped = {
        "ring": {"type": "环", "level": 110,
                 "affix_1": {"name": "会心率", "value": 5.0}},
        "pendant": {"type": "佩", "level": 110,
                    "affix_1": {"name": "会心率", "value": 7.0}},
    }
    effective = effective_equipped(equipped, _Proxy())
    assert "affix_1" not in effective["ring"]
    assert effective["pendant"]["affix_1"]["value"] == 7.0


def _rate(calc, equipped: dict) -> float:
    gc = get_game_config()
    attrs = build_graduation_attrs(
        CombatAttributes(),
        compute_equip_base_attrs(equipped, gc.get_base_attr_values)
        + aggregate_equipment_attrs(equipped),
        "鸣金·虹")
    return float(calc.calculate(attrs).graduation_rate)


def test_optimal_combo_applies_max_stacking_across_weapon_slots():
    from lvjiang.apps.yysls.core.graduation import (
        get_graduation_calculator,
        invalidate_graduation_cache,
    )

    invalidate_graduation_cache()
    calc = get_graduation_calculator("鸣金·虹", "基础方案")
    assert calc is not None
    candidates = {
        "main_weapon": [_sword("主A", 9.8), _sword("主B", 6.0)],
        "sub_weapon": [_sword("副A", 9.0), _sword("副B", 5.0)],
    }
    results = search_optimal_combo(
        candidates, calc, CombatAttributes(), use_dominance_pruning=False)
    assert results
    # 组合搜索给出的毕业率必须等于标准路径按“同名只取最高”算出的毕业率，
    # 而不是把两条剑武学增伤相加。
    for row in results:
        expected = _rate(calc, copy.deepcopy(row["equipped"]))
        assert abs(row["rate"] - expected) < 1e-9
    summed = aggregate_equipment_attrs(results[0]["equipped"], normalize=False)
    assert summed.extra_attrs["剑武学增伤"] > 0.1  # 原始数据确实有两条


def test_smart_tuning_candidate_uses_max_stacking_with_incumbent(monkeypatch):
    from lvjiang.apps.yysls.core.graduation.smart_tuning import (
        SmartTuningEvaluator,
        _PlanContext,
    )
    from tests.yysls.test_smart_tuning import _bare_evaluator

    evaluator = _bare_evaluator()
    gc = get_game_config()
    evaluator._game_config = gc
    seen: list[CombatAttributes] = []
    calculator = SimpleNamespace(calculate=lambda attrs: (
        seen.append(attrs) or SimpleNamespace(graduation_rate=0.5)))
    context = _PlanContext(
        "p", "方案", "鸣金·虹", calculator, CombatAttributes(),
        {"main_weapon": _sword("在位", 9.0),
         "sub_weapon": {"type": "枪", "level": 110,
                        "affix_1": {"name": "最大外功攻击", "value": 100}}},
        1.0, affix_pool=("A",), plan_maximum_rate=1.0, playstyle="")
    assert isinstance(evaluator, SmartTuningEvaluator)
    evaluator._candidate_rate(context, "sub_weapon", _sword("候选", 8.0))
    assert seen
    # 智能调律按三满口径：两条都升到 110 承音上限 9.212%，同名只取最高一条，
    # 再过增益抗性 1.15 → 0.0801；若分槽相加会得到两倍。
    caps = gc.get_affix_caps(110, "剑武学增伤")
    expected = caps["chengyin"] / 100 / 1.15
    assert abs(seen[-1].extra_attrs["剑武学增伤"] - expected) < 1e-9


def test_affix_caps_panel_edits_stack_mode(qtbot):
    from lvjiang.apps.yysls.ui.game_settings.affix_caps_panel import (
        AffixCapsPanel,
    )

    data = {"affix_caps": {
        "指定武学增效": {"_unit": "%", "_stack": "max",
                        "_aliases": ["剑武学增伤"], "110": {"cap": 9.8}},
        "全部武学增效": {"_unit": "%", "_aliases": ["全武学增效"],
                        "110": {"cap": 4.9}},
    }}
    changes: list[int] = []
    panel = AffixCapsPanel(data=data, on_changed=lambda: changes.append(1))
    qtbot.addWidget(panel)

    panel._affix_list.setCurrentRow(0)
    assert panel._radio_stack_max.isChecked()
    panel._affix_list.setCurrentRow(1)
    assert panel._radio_stack_sum.isChecked()

    panel._radio_stack_max.setChecked(True)
    assert data["affix_caps"]["全部武学增效"]["_stack"] == "max"
    panel._radio_stack_sum.setChecked(True)
    assert "_stack" not in data["affix_caps"]["全部武学增效"]
    assert changes
