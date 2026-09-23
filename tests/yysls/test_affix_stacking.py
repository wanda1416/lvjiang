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
        # 当前赛季等级的原生装备：钉死等阶会让「原生不被改成承音」这条
        # 前提在换赛季后悄悄失效，用例就不再测它自称在测的东西。
        "name": name, "type": "剑",
        "level": get_game_config().current_equip_level(), "quality": "gold",
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


def _buff_divisor() -> float:
    """当前赛季装备等级的增益抗性除数。

    抗性每个等阶都会变，写死只会让补数据的提交无端变红；这里验的是抗性有
    没有被施加，不是某一赛季的数字。
    """
    from lvjiang.apps.yysls.config import get_game_config

    gc = get_game_config()
    config = gc.level_config_for(gc.current_equip_level())
    return 1 + float((config and config.buff_resistance) or 0) / 100


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
         "sub_weapon": {"type": "枪",
                        "level": get_game_config().current_equip_level(),
                        "affix_1": {"name": "最大外功攻击", "value": 100}}},
        1.0, affix_pool=("A",), plan_maximum_rate=1.0, playstyle="")
    assert isinstance(evaluator, SmartTuningEvaluator)
    evaluator._candidate_rate(context, "sub_weapon", _sword("候选", 8.0))
    assert seen
    # 两件都是当前赛季原生装备，满承音不会把它们静态改成承音；同名词条
    # 仍只取在位装备较高的 9%，再过一遍增益抗性。若分槽相加会得到两倍。
    expected = 9.0 / 100 / _buff_divisor()
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


def test_affix_caps_panel_chengyin_is_editable_config_value(qtbot) -> None:
    """承音上限是配置原值：面板展示配置里的值、可改并写回；
    填完上限而承音为空时才按 94% 给默认值。"""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QTableWidgetItem

    from lvjiang.apps.yysls.ui.game_settings.affix_caps_panel import (
        AffixCapsPanel,
    )

    data = {"affix_caps": {
        "外功攻击": {"_aliases": ["最大外功攻击"],
                    "110": {"cap": 121.4, "chengyin": 114.2}},
    }}
    changes: list[int] = []
    panel = AffixCapsPanel(data=data, on_changed=lambda: changes.append(1))
    qtbot.addWidget(panel)
    panel._affix_list.setCurrentRow(0)

    chengyin_item = panel._table.item(0, 2)
    assert chengyin_item is not None
    assert chengyin_item.text() == "114.2"          # 配置原值，不是 94% 算出的 114.1
    assert chengyin_item.flags() & Qt.ItemFlag.ItemIsEditable

    chengyin_item.setText("114.0")
    # 同步回 _data 时等级 key 由 LevelCombo 给出（int）
    assert data["affix_caps"]["外功攻击"][110] == {"cap": 121.4, "chengyin": 114}
    assert "110" not in data["affix_caps"]["外功攻击"]
    assert changes

    # 新建等级：填完上限，承音自动给 94% 默认值，可再改
    panel._add_level()
    row = panel._table.rowCount() - 1
    panel._table.cellWidget(row, 0).set_level(105)
    panel._table.setItem(row, 1, QTableWidgetItem("105.6"))
    assert panel._table.item(row, 2).text() == "99.3"
    assert data["affix_caps"]["外功攻击"][105] == {"cap": 105.6, "chengyin": 99.3}
