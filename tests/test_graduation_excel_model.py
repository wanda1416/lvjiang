from __future__ import annotations

import json
from pathlib import Path

import pytest

from lvjiang.apps.yysls.combat_attrs import CombatAttributes
from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.evaluator.excel_formula import FormulaModel, parse_formula
from lvjiang.apps.yysls.evaluator.graduation import get_graduation_calculator

DATA_DIR = (
    Path(__file__).parents[1] / "config" / "system" / "yysls" / "graduation"
)
SCHOOLS = [
    "鸣金·虹", "鸣金·影", "裂石·威", "裂石·钧", "牵丝·玉",
    "牵丝·霖", "牵丝·翊", "破竹·尘", "破竹·风", "破竹·鸢", "破竹·樽",
]


def _load(school: str) -> dict:
    return json.loads(
        (DATA_DIR / f"{school}_基础方案.json").read_text(encoding="utf-8")
    )


def test_formula_parser_supports_workbook_subset() -> None:
    ast = parse_formula('=IF(A1>0,SUM(B1:B3),VLOOKUP("x",表!A:C,2,FALSE))')
    assert ast["op"] == "call"
    assert ast["name"] == "IF"


@pytest.mark.parametrize("school", SCHOOLS)
def test_converted_model_matches_excel_cached_outputs(school: str) -> None:
    model = _load(school)
    assert model["schema_version"] == "1.0"
    assert model["model"]["source"]["sha256"]
    runtime = FormulaModel(model)
    for name in ("combat_time", "total_damage", "dps", "rdps", "graduation_rate"):
        reference = model["outputs"][name]["ref"]
        actual = float(runtime.value(reference))
        sheet, coordinate = reference.split("!", 1)
        cell = model["sheets"][sheet]["cells"][coordinate]
        expected = float(cell.get("cached", cell.get("value")))
        assert actual == pytest.approx(expected, rel=1e-10, abs=1e-6)


def test_runtime_uses_non_mingjin_element_inputs() -> None:
    calculator = get_graduation_calculator("破竹·鸢")
    assert calculator is not None
    low = CombatAttributes(min_outer=3000, max_outer=3000,
                           min_pozhu=100, max_pozhu=100)
    high = CombatAttributes(min_outer=3000, max_outer=3000,
                            min_pozhu=1000, max_pozhu=1000)
    assert calculator.calculate(high).dps > calculator.calculate(low).dps


def test_runtime_reports_workbook_baseline() -> None:
    calculator = get_graduation_calculator("裂石·威")
    assert calculator is not None
    assert calculator.baseline_dps() == pytest.approx(141520.374878871)
    assert calculator.combat_time() == pytest.approx(101.4)


@pytest.mark.parametrize("school", SCHOOLS)
def test_dynamic_inputs_use_canonical_game_affix_names(school: str) -> None:
    model = _load(school)
    game_config = get_game_config()
    canonical = set(game_config.get_wuxue_affix_names())
    canonical.update(game_config.get_aliases_for_category("指定技能增效"))
    mapped_inputs = (
        "all_skill_bonus", "boss_bonus", "weapon_bonus_primary",
        "weapon_bonus_secondary", "single_qs_bonus", "group_qs_bonus",
        "special_bonus",
    )
    for name in mapped_inputs:
        spec = model["inputs"][name]
        sheet, coordinate = spec["label_ref"].split("!", 1)
        label_cell = model["sheets"][sheet]["cells"].get(coordinate, {})
        label = label_cell.get("value")
        affix_names = spec["affix_names"]
        assert affix_names == (
            game_config.get_affix_names_for_alias(label) if label else []
        )
        if name in {"weapon_bonus_primary", "weapon_bonus_secondary", "special_bonus"}:
            assert set(affix_names) <= canonical


def test_runtime_matches_mingjin_hong_excel_example() -> None:
    calculator = get_graduation_calculator("鸣金·虹")
    assert calculator is not None
    attrs = CombatAttributes(
        min_outer=1696, max_outer=5624, outer_pen=63.5,
        min_mingjin=513, max_mingjin=1350, mingjin_pen=36,
        mingjin_bonus=0.15, min_wuxiang=79, max_wuxiang=157,
        precision=0.8271, crit_rate=0.1789, intent_rate=0.3929,
        direct_crit=0, direct_intent=0.023,
        crit_dmg=0.5, intent_dmg=0.402,
        all_skill_bonus=0.08, boss_bonus=0.083,
        extra_attrs={
            "剑武学增伤": 0.08,
            "无名剑法蓄力技增伤": 0.16,
            "无名枪法蓄力技增伤": 0.16,
        },
    )
    result = calculator.calculate(attrs)
    assert result.dps == pytest.approx(119459.79969686334)
    assert result.graduation_rate == pytest.approx(0.9907868092668608)


def test_runtime_maps_real_affix_names_to_excel_short_labels() -> None:
    calculator = get_graduation_calculator("鸣金·虹")
    assert calculator is not None
    common = dict(
        min_outer=1696, max_outer=5624, outer_pen=63.5,
        min_mingjin=513, max_mingjin=1350, mingjin_pen=36,
        mingjin_bonus=0.15, min_wuxiang=79, max_wuxiang=157,
        precision=0.8271, crit_rate=0.1789, intent_rate=0.3929,
        direct_crit=0, direct_intent=0.023,
        crit_dmg=0.5, intent_dmg=0.402,
        all_skill_bonus=0.08, boss_bonus=0.083,
    )
    configured = CombatAttributes(
        **common,
        extra_attrs={
            "剑武学增伤": 0.08,
            "无名剑法蓄力技增伤": 0.16,
            "无名枪法蓄力技增伤": 0.16,
        },
    )
    scanned = CombatAttributes(
        **common,
        extra_attrs={
            "剑武学增伤": 0.08,
            "无名剑法蓄力技增伤": 0.16,
            "无名枪法蓄力技增伤": 0.16,
            "文动霓裳特殊技增伤": 0.99,
        },
    )
    assert calculator.calculate(scanned) == calculator.calculate(configured)
