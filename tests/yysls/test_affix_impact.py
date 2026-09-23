"""当前配装词条边际收益分析测试。"""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.core.combat.combat_attrs import CombatAttributes
from lvjiang.apps.yysls.core.graduation.affix_impact import (
    AffixBlockedEquipment,
    AffixCombinationResult,
    AffixImpactReport,
    AffixReplacementSuggestion,
    analyze_affix_impacts,
    analyze_combined_affix_replacements,
)
from tests.case_matrix import case_matrix


class _LinearCalculator:
    """只关心属性增减的线性测试计算器。"""

    def calculate(self, attrs: CombatAttributes):
        rate = (
            attrs.min_outer / 10000
            + attrs.max_outer / 10000
            + attrs.boss_bonus
            + attrs.all_skill_bonus
            + sum(attrs.extra_attrs.values())
        )
        return SimpleNamespace(graduation_rate=rate)


class _PreferMinimumCalculator:
    def calculate(self, attrs: CombatAttributes):
        return SimpleNamespace(
            graduation_rate=attrs.min_outer / 5000 + attrs.max_outer / 10000,
        )


def _equipment(*, sword_bonus: float | None = 9.8) -> dict:
    sword = {
        "type": "剑", "level": 110, "quality": "gold",
        "affix_1": {"name": "最大外功攻击", "value": 100},
        "affix_2": {"name": "最大外功攻击", "value": 100},
    }
    if sword_bonus is not None:
        sword["affix_3"] = {"name": "剑武学增伤", "value": sword_bonus}
    return {
        "main_weapon": sword,
        "sub_weapon": {
            "type": "枪", "level": 110, "quality": "gold",
            "affix_1": {"name": "最小外功攻击", "value": 80},
        },
        "leg": {
            "type": "胫甲", "level": 110, "quality": "gold",
            "affix_1": {"name": "劲", "value": 70},
            "affix_2": {"name": "对首领单位增伤", "value": 4.2},
        },
    }


def test_analysis_only_removes_affixes_that_are_actually_equipped():
    report = analyze_affix_impacts(
        _equipment(),
        _LinearCalculator(),
        CombatAttributes(),
        "鸣金·虹",
        game_config=get_game_config(),
        affix_level=110,
    )

    removal_names = {item.name for item in report.removals}
    assert removal_names == {
        "剑武学增伤", "最大外功攻击", "最小外功攻击", "对首领单位增伤",
        "劲",
    }
    assert "会意率" not in removal_names
    assert all(item.graduation_delta <= 0 for item in report.removals)


def test_existing_weapon_bonus_cannot_be_added_again():
    report = analyze_affix_impacts(
        _equipment(),
        _LinearCalculator(),
        CombatAttributes(),
        "鸣金·虹",
        game_config=get_game_config(),
        affix_level=110,
    )

    addition_names = {item.name for item in report.additions}
    assert "剑武学增伤" not in addition_names
    assert "枪武学增伤" in addition_names
    assert not addition_names.intersection({
        "陌刀武学增伤", "横刀武学增伤", "伞武学增效", "扇武学增效",
    })


def test_missing_weapon_bonus_is_addable_but_not_removable():
    report = analyze_affix_impacts(
        _equipment(sword_bonus=None),
        _LinearCalculator(),
        CombatAttributes(),
        "鸣金·虹",
        game_config=get_game_config(),
        affix_level=110,
    )

    assert "剑武学增伤" in {item.name for item in report.additions}
    assert "剑武学增伤" not in {item.name for item in report.removals}


def _buff_divisor() -> float:
    """当前赛季装备等级的增益抗性除数。

    抗性每个等阶都会变，写死只会让补数据的提交无端变红；这里验的是抗性有
    没有被施加，不是某一赛季的数字。
    """
    from lvjiang.apps.yysls.config import get_game_config

    gc = get_game_config()
    config = gc.level_config_for(gc.current_equip_level())
    return 1 + float((config and config.buff_resistance) or 0) / 100


def test_duplicate_same_weapon_bonus_only_uses_the_best_one():
    equipped = _equipment()
    equipped["sub_weapon"] = {
        "type": "剑", "level": 110, "quality": "gold",
        "affix_1": {"name": "最大外功攻击", "value": 100},
        "affix_2": {"name": "剑武学增伤", "value": 7.0},
    }

    report = analyze_affix_impacts(
        equipped,
        _LinearCalculator(),
        CombatAttributes(),
        "鸣金·虹",
        game_config=get_game_config(),
        affix_level=110,
    )

    sword = next(item for item in report.removals if item.name == "剑武学增伤")
    # 扣除 9.8% 后 7.0% 接替生效；再过一遍增益抗性。
    assert sword.graduation_delta == pytest.approx(-0.028 / _buff_divisor())
    assert "剑武学增伤" not in {item.name for item in report.additions}


def test_cultivation_suggests_best_legal_equal_quality_replacement():
    report = analyze_affix_impacts(
        _equipment(),
        _PreferMinimumCalculator(),
        CombatAttributes(),
        "鸣金·虹",
        game_config=get_game_config(),
        affix_level=110,
    )

    suggestion = next(
        item for item in report.suggestions
        if item.slot_key == "main_weapon" and item.affix_index == 2
    )
    assert suggestion.from_name == "最大外功攻击"
    assert suggestion.to_name == "最小外功攻击"
    assert suggestion.to_value == pytest.approx(100)
    assert suggestion.cap_pct == pytest.approx(100 / 121.4 * 100)
    assert suggestion.graduation_delta == pytest.approx(0.01)


def test_cultivation_does_not_replace_first_affix_or_chengyin_equipment():
    equipped = _equipment()
    equipped["main_weapon"]["is_chengyin"] = True

    report = analyze_affix_impacts(
        equipped,
        _PreferMinimumCalculator(),
        CombatAttributes(),
        "鸣金·虹",
        game_config=get_game_config(),
        affix_level=110,
    )

    assert all(item.slot_key != "main_weapon" for item in report.suggestions)
    assert all(item.affix_index >= 2 for item in report.suggestions)


def test_cultivation_does_not_create_duplicate_affixes_2_to_5():
    equipped = _equipment()
    equipped["main_weapon"]["affix_3"] = {
        "name": "最小外功攻击", "value": 100,
    }

    report = analyze_affix_impacts(
        equipped,
        _PreferMinimumCalculator(),
        CombatAttributes(),
        "鸣金·虹",
        game_config=get_game_config(),
        affix_level=110,
    )

    source = [
        item for item in report.suggestions
        if item.slot_key == "main_weapon" and item.affix_index == 2
    ]
    assert all(item.to_name != "最小外功攻击" for item in source)


def test_cultivation_never_suggests_divine_affix_from_transmutation():
    report = analyze_affix_impacts(
        _equipment(),
        _LinearCalculator(),
        CombatAttributes(),
        "鸣金·虹",
        game_config=get_game_config(),
        affix_level=110,
    )
    divine = {"增效类", "武器类"}
    game_config = get_game_config()

    assert all(
        game_config.get_affix_category(item.to_name) not in divine
        for item in report.suggestions
    )


def _open_dialog(report, school="鸣金·虹", scheme="基础方案", **kwargs):
    """培养建议页按需构建；测试直接触发该页构建并返回页组。"""
    from lvjiang.apps.yysls.ui.loadout.affix_analysis_pages import (
        AffixAnalysisPages,
    )

    pages = AffixAnalysisPages(
        school, scheme, report_provider=lambda: report, **kwargs)
    pages.ensure_built(pages.suggestion_page)
    return pages


def _replacement(slot: str, index: int = 2) -> AffixReplacementSuggestion:
    return AffixReplacementSuggestion(
        slot_key=slot,
        equipment_name=slot,
        affix_index=index,
        from_name="最大外功攻击",
        from_value=100,
        to_name="最小外功攻击",
        to_value=100,
        cap_pct=82.4,
        graduation_delta=0.01,
    )


@case_matrix("slots", [
    ("main_weapon", "ring"),
    ("main_weapon", "ring", "pendant"),
])
def test_joint_cultivation_recalculates_two_or_three_equipment(slots):
    equipped = _equipment()
    equipped["ring"] = {
        "type": "环", "level": 110,
        "affix_1": {"name": "最大外功攻击", "value": 100},
        "affix_2": {"name": "最大外功攻击", "value": 100},
    }
    equipped["pendant"] = {
        "type": "佩", "level": 110,
        "affix_1": {"name": "最大外功攻击", "value": 100},
        "affix_2": {"name": "最大外功攻击", "value": 100},
    }
    suggestions = tuple(_replacement(slot) for slot in slots)

    result = analyze_combined_affix_replacements(
        equipped,
        suggestions,
        slots,
        _PreferMinimumCalculator(),
        CombatAttributes(),
        "鸣金·虹",
        game_config=get_game_config(),
    )

    assert result.graduation_delta >= 0.01 * len(slots)
    assert set(slots).issubset({item.slot_key for item in result.replacements})
    assert result.evaluated_combinations > 2 ** len(slots) - 1


def test_joint_cultivation_changes_at_most_one_slot_per_equipment():
    """一次转律只能改一件装备的一个槽，联合方案不得在同件上叠加多槽。"""
    equipped = _equipment()
    equipped["main_weapon"]["affix_3"] = {
        "name": "会心率", "value": 14,
    }

    result = analyze_combined_affix_replacements(
        equipped,
        (),
        ("main_weapon",),
        _PreferMinimumCalculator(),
        CombatAttributes(),
        "鸣金·虹",
        game_config=get_game_config(),
    )

    assert len(result.replacements) == 1
    assert result.replacements[0].slot_key == "main_weapon"
    assert result.graduation_delta > 0
    changed = copy.deepcopy(equipped)
    for item in result.replacements:
        changed[item.slot_key][f"affix_{item.affix_index}"] = {
            "name": item.to_name, "value": item.to_value,
            "is_transferred": True,
        }
    from lvjiang.apps.yysls.core.equip_validator import validate_combination_dict
    assert validate_combination_dict(changed["main_weapon"]) == []


def test_joint_cultivation_rejects_more_than_three_equipment():
    with pytest.raises(ValueError, match="1-3"):
        analyze_combined_affix_replacements(
            _equipment(),
            (),
            ("main_weapon", "sub_weapon", "ring", "pendant"),
            _PreferMinimumCalculator(),
            CombatAttributes(),
            "鸣金·虹",
            game_config=get_game_config(),
        )


def test_joint_cultivation_rejects_any_invalid_baseline_equipment():
    equipped = _equipment()
    equipped.update(_flawed_equipment())

    with pytest.raises(ValueError, match="请先校正"):
        analyze_combined_affix_replacements(
            equipped,
            (_replacement("main_weapon"),),
            ("main_weapon",),
            _PreferMinimumCalculator(),
            CombatAttributes(),
            "鸣金·虹",
            game_config=get_game_config(),
        )


def test_runtime_analysis_dependencies_resolve_from_equip_package():
    """防止延迟导入误解析为不存在的 ``yysls.ui.config/core``。"""
    from lvjiang.apps.yysls.ui.loadout.equip.status_tab import (
        _affix_analysis_dependencies,
    )

    dependencies = _affix_analysis_dependencies()
    assert len(dependencies) == 9
    assert all(callable(dependency) for dependency in dependencies)


def test_dialog_limits_joint_selection_to_three_equipment(qtbot):
    from PyQt6.QtWidgets import QCheckBox, QLabel


    suggestions = tuple(
        _replacement(slot)
        for slot in ("main_weapon", "sub_weapon", "ring", "pendant")
    )
    report = AffixImpactReport(0.8, 110, (), (), suggestions)
    dialog = _open_dialog(
        report,
        joint_analyzer=lambda slots: AffixCombinationResult(
            slots, 0.8, 0.0, (), 0,
        ),
    )
    qtbot.addWidget(dialog)

    checks = [
        dialog.findChild(QCheckBox, f"affixSlotCheck_{slot}")
        for slot in ("main_weapon", "sub_weapon", "ring", "pendant")
    ]
    assert all(check is not None for check in checks)
    for check in checks:
        assert check is not None
        check.click()

    assert sum(check.isChecked() for check in checks if check is not None) == 3
    detail = dialog.findChild(QLabel, "jointAffixDetail")
    assert detail is not None
    assert "最多" in detail.text()


def test_dialog_never_labels_negative_delta_as_cultivation_advice(qtbot):
    from PyQt6.QtWidgets import QCheckBox, QLabel


    negative = AffixReplacementSuggestion(
        slot_key="ring",
        equipment_name="测试环",
        affix_index=2,
        from_name="最大外功攻击",
        from_value=100,
        to_name="会心率",
        to_value=14,
        cap_pct=100,
        graduation_delta=-0.02,
    )
    dialog = _open_dialog(
        AffixImpactReport(0.8, 110, (), (), (negative,)), "破竹·鸢")
    qtbot.addWidget(dialog)

    count = dialog.findChild(QLabel, "affixMetricValue_count")
    assert count is not None
    assert count.text() == "0 条"
    assert dialog.findChild(QCheckBox, "affixSlotCheck_ring") is None


def test_joint_button_is_clickable_and_explains_missing_analyzer(qtbot):
    from PyQt6.QtWidgets import QCheckBox, QLabel, QPushButton


    report = AffixImpactReport(0.8, 110, (), (), (_replacement("ring"),))
    dialog = _open_dialog(report, "破竹·鸢")
    qtbot.addWidget(dialog)
    checkbox = dialog.findChild(QCheckBox, "affixSlotCheck_ring")
    button = dialog.findChild(QPushButton, "calculateJointAffixButton")
    detail = dialog.findChild(QLabel, "jointAffixDetail")
    assert checkbox is not None
    assert button is not None
    assert detail is not None

    checkbox.click()
    assert button.isEnabled()
    button.click()
    assert "未初始化" in detail.text()


# ─── 已有违规的装备仍要给出培养建议 ────────────────────────

def _flawed_equipment() -> dict:
    """一件首词条被 OCR 误读成非该部位首词条的胸甲

    这正是 illegal badge 存在的理由：首词条误读。除首词条外全部合法，
    完全应该照常给培养建议。
    """
    return {
        "chest": {
            "type": "胸甲", "level": 110, "quality": "gold",
            "affix_1": {"name": "最大外功攻击", "value": 100},
            "affix_2": {"name": "会意率", "value": 6.0},
            "affix_3": {"name": "劲", "value": 70},
        },
    }


def test_existing_flaw_blocks_suggestions_and_reports_reason():
    """异常装备的毕业率输入不可信，必须先校正，不能输出非法培养方案。"""
    from lvjiang.apps.yysls.core.equip_validator import validate_combination_dict

    equipped = _flawed_equipment()
    # 前提：这件装备确实带违规，否则本用例失去意义
    flaws = validate_combination_dict(equipped["chest"])
    assert [r.code for r in flaws] == ["invalid_first_affix"]
    result = analyze_affix_impacts(
        equipped, _LinearCalculator(), CombatAttributes(), "鸣金·虹",
    )
    assert result.suggestions == ()
    assert len(result.blocked_equipment) == 1
    assert result.blocked_equipment[0].slot_key == "chest"
    assert "首词条" in result.blocked_equipment[0].reasons[0]


def test_all_suggested_replacements_are_strictly_legal():
    equipped = _equipment()
    result = analyze_affix_impacts(
        equipped, _LinearCalculator(), CombatAttributes(), "鸣金·虹",
    )
    for item in result.suggestions:
        changed = copy.deepcopy(equipped)
        changed[item.slot_key][f"affix_{item.affix_index}"] = {
            "name": item.to_name, "value": item.to_value, "is_transferred": True,
        }
        from lvjiang.apps.yysls.core.equip_validator import (
            validate_combination_dict,
        )
        assert validate_combination_dict(changed[item.slot_key]) == []


def test_dialog_explains_blocked_equipment(qtbot):
    from PyQt6.QtWidgets import QLabel


    report = AffixImpactReport(
        0.8,
        110,
        (),
        (),
        blocked_equipment=(AffixBlockedEquipment(
            "chest", "测试胸甲", ("首词条不合法",),
        ),),
    )
    dialog = _open_dialog(report)
    qtbot.addWidget(dialog)

    texts = [label.text() for label in dialog.findChildren(QLabel)]
    assert any("测试胸甲" in text and "首词条不合法" in text for text in texts)
    current_rate = dialog.findChild(QLabel, "affixMetricValue_rate")
    assert current_rate is not None
    assert current_rate.text() == "需先校正"
