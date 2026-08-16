"""Convert graduation workbooks into executable, named JSON schemes."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import openpyxl
import yaml
from openpyxl.worksheet.formula import ArrayFormula

from lvjiang.constants import PROJECT_ROOT

from .excel_formula import FormulaError, FormulaModel, parse_formula

GRADUATION_DIR = PROJECT_ROOT / "config" / "system" / "yysls" / "graduation"
GAME_CONFIG_PATH = PROJECT_ROOT / "config" / "system" / "yysls" / "game_config.yaml"

INPUTS = {
    "min_outer": "期望!B2", "max_outer": "期望!C2",
    "outer_pen": "期望!D2", "outer_bonus": "期望!E2",
    "min_mingjin": "期望!B3", "max_mingjin": "期望!C3",
    "mingjin_pen": "期望!D3", "mingjin_bonus": "期望!E3",
    "min_lieshi": "期望!B4", "max_lieshi": "期望!C4",
    "lieshi_pen": "期望!D4", "lieshi_bonus": "期望!E4",
    "min_qiansi": "期望!B5", "max_qiansi": "期望!C5",
    "qiansi_pen": "期望!D5", "qiansi_bonus": "期望!E5",
    "min_pozhu": "期望!B6", "max_pozhu": "期望!C6",
    "pozhu_pen": "期望!D6", "pozhu_bonus": "期望!E6",
    "min_wuxiang": "期望!B7", "max_wuxiang": "期望!C7",
    "precision": "期望!B8", "crit_rate": "期望!B9",
    "intent_rate": "期望!B10", "direct_crit": "期望!B11",
    "direct_intent": "期望!B12", "crit_dmg": "期望!B13",
    "intent_dmg": "期望!B14", "all_skill_bonus": "期望!B15",
    "boss_bonus": "期望!B16", "weapon_bonus_primary": "期望!B17",
    "weapon_bonus_secondary": "期望!B18", "single_qs_bonus": "期望!B19",
    "group_qs_bonus": "期望!B20", "special_bonus": "期望!B21",
}
RATIO_INPUTS = {
    "outer_bonus", "mingjin_bonus", "lieshi_bonus", "qiansi_bonus",
    "pozhu_bonus", "precision", "crit_rate", "intent_rate", "direct_crit",
    "direct_intent", "crit_dmg", "intent_dmg", "all_skill_bonus",
    "boss_bonus", "single_qs_bonus", "group_qs_bonus",
}
OUTPUTS = {
    "combat_time": "期望!I8", "total_damage": "期望!I10",
    "dps": "期望!I12", "rdps": "期望!I14",
    "graduation_rate": "期望!I16",
}


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _version(filename: str) -> str:
    match = re.search(r"(\d+\.\d+)\.xlsx$", filename)
    return match.group(1) if match else "unknown"


def _affix_input_names(workbook, school: str) -> dict[str, list[str]]:
    config = yaml.safe_load(GAME_CONFIG_PATH.read_text(encoding="utf-8"))
    labels = {
        "all_skill_bonus": workbook["期望"]["A15"].value,
        "boss_bonus": workbook["期望"]["A16"].value,
        "weapon_bonus_primary": workbook["期望"]["A17"].value,
        "weapon_bonus_secondary": workbook["期望"]["A18"].value,
        "single_qs_bonus": workbook["期望"]["A19"].value,
        "group_qs_bonus": workbook["期望"]["A20"].value,
        "special_bonus": workbook["期望"]["A21"].value,
    }
    canonical_names: set[str] = set()
    for category in (config.get("affix_caps") or {}).values():
        raw = category.get("_aliases", []) if isinstance(category, dict) else []
        groups = raw.values() if isinstance(raw, dict) else [raw]
        for names in groups:
            if isinstance(names, list):
                canonical_names.update(str(name) for name in names)
    alias_index: dict[str, list[str]] = {}
    for affix_name, aliases in (config.get("affix_aliases") or {}).items():
        if affix_name not in canonical_names:
            raise RuntimeError(f"affix_aliases uses unknown affix: {affix_name!r}")
        if not isinstance(aliases, list):
            raise RuntimeError(f"affix_aliases[{affix_name!r}] must be a list")
        for alias in aliases:
            alias_index.setdefault(str(alias), []).append(str(affix_name))
    result: dict[str, list[str]] = {}
    for input_name, label in labels.items():
        if label is None and input_name == "special_bonus":
            result[input_name] = []
            continue
        matches = alias_index.get(str(label), [])
        if not matches:
            raise RuntimeError(
                f"{school} Excel label {label!r} has no affix_aliases mapping"
            )
        result[input_name] = matches
    return result


def convert_workbook(path: Path, school: str) -> dict[str, Any]:
    formulas = openpyxl.load_workbook(path, data_only=False, read_only=False)
    cached = openpyxl.load_workbook(path, data_only=True, read_only=False)
    try:
        affix_names = _affix_input_names(formulas, school)
        sheets: dict[str, Any] = {}
        formula_count = 0
        for worksheet in formulas.worksheets:
            cached_sheet = cached[worksheet.title]
            cells: dict[str, Any] = {}
            for row in worksheet.iter_rows():
                for cell in row:
                    value = cell.value
                    if value is None:
                        continue
                    formula = value.text if isinstance(value, ArrayFormula) else value
                    if isinstance(formula, str) and formula.startswith("="):
                        parse_formula(formula)
                        entry: dict[str, Any] = {"formula": formula}
                        cached_value = cached_sheet[cell.coordinate].value
                        if cached_value is not None:
                            entry["cached"] = _json_value(cached_value)
                        formula_count += 1
                    else:
                        entry = {"value": _json_value(value)}
                    cells[cell.coordinate] = entry
            sheets[worksheet.title] = {
                "dimensions": {
                    "max_row": worksheet.max_row,
                    "max_column": worksheet.max_column,
                },
                "cells": cells,
            }
        source_bytes = path.read_bytes()
        return {
            "schema_version": "1.0",
            "formula_language": "excel_subset_v1",
            "model": {
                "id": school.replace("·", ""), "school": school,
                "source": {
                    "file": path.name, "version": _version(path.name),
                    "sha256": hashlib.sha256(source_bytes).hexdigest(),
                },
                "formula_count": formula_count,
            },
            "inputs": {
                name: {
                    "type": "number",
                    "unit": "ratio" if name in RATIO_INPUTS or name in {
                        "weapon_bonus_primary", "weapon_bonus_secondary",
                        "special_bonus",
                    } else "point",
                    "target": target,
                    **({"label_ref": f"期望!A{target.rsplit('B', 1)[-1]}",
                        "affix_names": affix_names[name]}
                       if name in affix_names else {}),
                }
                for name, target in INPUTS.items()
            },
            "outputs": {name: {"ref": ref} for name, ref in OUTPUTS.items()},
            "sheets": sheets,
        }
    finally:
        formulas.close()
        cached.close()


def validate_model(model: dict[str, Any]) -> dict[str, float]:
    runtime = FormulaModel(model)
    results: dict[str, float] = {}
    for name, spec in model["outputs"].items():
        result = runtime.value(spec["ref"])
        results[name] = float(result) if result is not None else 0.0
        sheet, coordinate = spec["ref"].split("!", 1)
        cached = model["sheets"][sheet]["cells"][coordinate].get("cached")
        if cached is not None and abs(float(cached) - results[name]) > max(
            1e-6, abs(float(cached)) * 1e-10,
        ):
            raise FormulaError(
                f"{spec['ref']} evaluated to {results[name]}, cached value is {cached}"
            )
    return results


def scheme_path(school: str, scheme: str) -> Path:
    cleaned = scheme.strip()
    if not cleaned or any(char in cleaned for char in '<>:"/\\|?*'):
        raise ValueError("方案名称为空或包含文件名非法字符")
    return GRADUATION_DIR / f"{school}_{cleaned}.json"


def write_model(path: Path, model: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(model, ensure_ascii=False, indent=2) + "\n"
    fd, temp_name = tempfile.mkstemp(dir=path.parent, suffix=".json.tmp")
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def import_graduation_scheme(
    excel_path: str | Path, school: str, scheme: str,
) -> tuple[Path, dict[str, float]]:
    source = Path(excel_path)
    if source.suffix.lower() != ".xlsx" or not source.is_file():
        raise ValueError("请选择有效的 .xlsx 文件")
    model = convert_workbook(source, school)
    outputs = validate_model(model)
    destination = scheme_path(school, scheme)
    write_model(destination, model)
    return destination, outputs
