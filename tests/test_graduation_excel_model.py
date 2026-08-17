from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from lvjiang.apps.yysls.core.combat.combat_attrs import CombatAttributes
from lvjiang.apps.yysls.config import get_game_config
from lvjiang.apps.yysls.core.graduation.excel_formula import parse_formula
from lvjiang.apps.yysls.core.graduation import (
    get_graduation_calculator,
    get_graduation_scheme_combat_attrs,
    get_graduation_scheme_inputs,
    invalidate_graduation_cache,
    set_graduation_baseline_dps,
)
from lvjiang.apps.yysls.core.graduation.graduation_converter import convert_workbook
from lvjiang.apps.yysls.core.graduation.graduation_program import ProgramRuntime

DATA_DIR = (
    Path(__file__).parents[1] / "config" / "system" / "yysls" / "graduation"
)
EXCEL_DIR = Path(__file__).parents[1] / "data" / "temp" / "excel"
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


def test_converter_resolves_skill_alias_in_school_group() -> None:
    path = next(
        path for path in EXCEL_DIR.glob("*鸣金虹*.xlsx")
        if "副本" not in path.stem
    )
    model = convert_workbook(path, "鸣金·虹")
    assert model["baseline_attrs"]["extra_attrs"]["无名剑法蓄力技增伤"] == 0.32


def test_converter_ignores_blank_skill_affix_label() -> None:
    path = next(EXCEL_DIR.glob("*牵丝霖*.xlsx"))
    model = convert_workbook(path, "牵丝·霖")
    skill_group = set(
        get_game_config().get_alias_groups("指定技能增效")["牵丝·霖"]
    )
    assert not skill_group.intersection(model["baseline_attrs"].get("extra_attrs", {}))


@pytest.mark.parametrize("school", SCHOOLS)
def test_converted_model_matches_excel_cached_outputs(school: str) -> None:
    model = _load(school)
    assert model["schema_version"] == 2
    assert model["source"]["sha256"]
    assert "sheets" not in model
    baseline = model["baseline_attrs"]
    extra = baseline.get("extra_attrs", {})
    values = [
        baseline.get(spec["name"], 0) if spec["kind"] == "field"
        else extra.get(spec["name"], 0)
        for spec in model["program"]["inputs"]
    ]
    actual = ProgramRuntime(model["program"], values).outputs()
    actual["graduation_rate"] = (
        actual["dps"] / model["graduation_baseline_dps"]
    )
    for name, expected in model["reference"].items():
        assert actual[name] == pytest.approx(expected, rel=1e-10, abs=1e-6)
    assert f"{actual['graduation_rate'] * 100:.2f}%" == "100.00%"


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
    assert calculator.baseline_dps() == pytest.approx(141520.37)
    assert calculator.combat_time() == pytest.approx(101.4)


def test_editable_baseline_dps_recalibrates_graduation_rate(
    tmp_path, monkeypatch,
) -> None:
    import lvjiang.apps.yysls.core.graduation as graduation

    source = DATA_DIR / "鸣金·虹_基础方案.json"
    shutil.copy(source, tmp_path / source.name)
    monkeypatch.setattr(graduation, "_DATA_DIR", tmp_path)
    invalidate_graduation_cache()
    try:
        attrs = get_graduation_scheme_combat_attrs("鸣金·虹", "基础方案")
        original = get_graduation_calculator("鸣金·虹", "基础方案")
        assert original is not None
        dps = original.calculate(attrs).dps
        set_graduation_baseline_dps("鸣金·虹", "基础方案", dps * 2)
        calibrated = get_graduation_calculator("鸣金·虹", "基础方案")
        assert calibrated is not None
        result = calibrated.calculate(attrs)
        assert result.dps == pytest.approx(dps)
        assert result.graduation_rate == pytest.approx(0.5)
    finally:
        invalidate_graduation_cache()


@pytest.mark.parametrize("school", SCHOOLS)
def test_dynamic_inputs_use_canonical_game_affix_names(school: str) -> None:
    model = _load(school)
    game_config = get_game_config()
    canonical = set(game_config.get_wuxue_affix_names())
    canonical.update(game_config.get_aliases_for_category("指定技能增效"))
    affix_inputs = {
        spec["name"] for spec in model["program"]["inputs"]
        if spec["kind"] == "affix"
    }
    assert affix_inputs <= canonical
    assert affix_inputs <= set(model["baseline_attrs"].get("extra_attrs", {}))


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
            "无名剑法蓄力技增伤": 0.32,
        },
    )
    result = calculator.calculate(attrs)
    assert result.dps == pytest.approx(119459.79969686334)
    assert result.graduation_rate == pytest.approx(0.9907868092668608)


def test_scheme_value_inputs_exclude_food_bonus() -> None:
    values = get_graduation_scheme_inputs("鸣金·虹", "基础方案")
    names = {entry["name"] for entry in values}
    assert "min_outer" in names
    assert "无名剑法蓄力技增伤" in names
    assert all("food" not in name.lower() for name in names)


def test_v2_records_environment_without_exposing_it_as_inputs() -> None:
    model = _load("鸣金·虹")
    assert model["environment"]["food_bonus"] == {
        "min_outer": 200,
        "max_outer": 400,
    }
    assert model["environment"]["team_buffs"]
    input_names = {spec["name"] for spec in model["program"]["inputs"]}
    assert "food_bonus" not in input_names
    assert "team_buffs" not in input_names


@pytest.mark.parametrize("school", SCHOOLS)
def test_v2_contains_no_excel_affix_aliases(school: str) -> None:
    raw = (DATA_DIR / f"{school}_基础方案.json").read_text(encoding="utf-8")
    game_config = get_game_config()
    for exact_name in game_config.get_wuxue_affix_names():
        for alias in game_config.get_affix_aliases(exact_name):
            assert f'"{alias}"' not in raw


def test_scheme_combat_attrs_only_expose_canonical_affix_names() -> None:
    attrs = get_graduation_scheme_combat_attrs("鸣金·虹", "基础方案")
    assert "剑武学增伤" in attrs.extra_attrs
    assert "无名剑法蓄力技增伤" in attrs.extra_attrs
    assert "剑增" not in attrs.extra_attrs
    assert "蓄力技定音" not in attrs.extra_attrs


def test_pozhu_scheme_uses_canonical_field_spelling() -> None:
    values = {
        entry["name"]: entry["value"]
        for entry in get_graduation_scheme_inputs("破竹·风", "基础方案")
    }
    attrs = get_graduation_scheme_combat_attrs("破竹·风", "基础方案")
    assert attrs.pozhu_pen == values["pozhu_pen"]
    assert attrs.pozhu_bonus == values["pozhu_bonus"]
    assert "pozhu_pen" in attrs.to_dict()


def test_runtime_only_consumes_canonical_affix_names() -> None:
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
            "无名剑法蓄力技增伤": 0.32,
        },
    )
    scanned = CombatAttributes(
        **common,
        extra_attrs={
            "剑武学增伤": 0.08,
            "无名剑法蓄力技增伤": 0.32,
            "文动霓裳特殊技增伤": 0.99,
        },
    )
    assert calculator.calculate(scanned) == calculator.calculate(configured)
