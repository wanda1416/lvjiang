"""Excel-model-backed DPS and graduation-rate calculator."""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from loguru import logger

from ..combat_attrs import (
    CombatAttributes,
)
from .excel_formula import FormulaModel

_DATA_DIR = (
    Path(__file__).parents[5] / "config" / "system" / "yysls" / "graduation"
)
_ALL_SCHOOLS = {
    "鸣金·虹", "鸣金·影", "裂石·威", "裂石·钧", "牵丝·玉",
    "牵丝·霖", "牵丝·翊", "破竹·尘", "破竹·风", "破竹·鸢", "破竹·樽",
}


@dataclass
class GraduationResult:
    total_damage: float
    dps: float
    graduation_rate: float
    baseline_dps: float
    combat_time: float


class GraduationCalculator(ABC):
    @abstractmethod
    def calculate(self, attrs: CombatAttributes) -> GraduationResult:
        """Calculate DPS and graduation rate for final combat attributes."""

    @abstractmethod
    def baseline_dps(self) -> float:
        """Return the workbook's reference DPS."""

    @abstractmethod
    def combat_time(self) -> float:
        """Return the workbook's configured combat duration."""


class GenericCalculator(GraduationCalculator):
    """Execute a converted workbook model with combat-attribute overrides."""

    def __init__(self, school_name: str, scheme_name: str) -> None:
        self._school = school_name
        self._scheme = scheme_name
        self._data = self._load_data(school_name, scheme_name)
        if self._data.get("schema_version") != "1.0":
            raise ValueError(f"unsupported graduation model for {school_name}")
        self._baseline = self._cached_output("dps")
        self._combat_time = self._cached_output("combat_time")

    @staticmethod
    @lru_cache(maxsize=len(_ALL_SCHOOLS))
    def _load_data(school_name: str, scheme_name: str) -> dict[str, Any]:
        path = _DATA_DIR / f"{school_name}_{scheme_name}.json"
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)

    def _cached_output(self, name: str) -> float:
        reference = self._data["outputs"][name]["ref"]
        sheet, coordinate = reference.split("!", 1)
        cell = self._data["sheets"][sheet]["cells"][coordinate]
        value = cell.get("cached", cell.get("value"))
        if value is None:
            value = FormulaModel(self._data).value(reference)
        return float(value)

    def baseline_dps(self) -> float:
        return self._baseline

    def combat_time(self) -> float:
        return self._combat_time

    def calculate(self, attrs: CombatAttributes) -> GraduationResult:
        values = _combat_attrs_to_model_inputs(attrs, self._data)
        overrides = {
            spec["target"]: values[name]
            for name, spec in self._data["inputs"].items()
            if name in values
        }
        runtime = FormulaModel(self._data, overrides)
        # 当前结果模型和界面均不消费 RDPS；跳过这条独立且昂贵的公式链。
        output_names = ("combat_time", "total_damage", "dps", "graduation_rate")
        outputs = {
            name: float(runtime.value(self._data["outputs"][name]["ref"]))
            for name in output_names
        }
        return GraduationResult(
            total_damage=outputs["total_damage"],
            dps=outputs["dps"],
            graduation_rate=outputs["graduation_rate"],
            baseline_dps=self._baseline,
            combat_time=outputs["combat_time"],
        )


def _combat_attrs_to_model_inputs(
    attrs: CombatAttributes, model: dict[str, Any],
) -> dict[str, float]:
    """Map Excel-ready effective combat attributes to model inputs."""
    values = {
        "min_outer": attrs.min_outer, "max_outer": attrs.max_outer,
        "outer_pen": attrs.outer_pen,
        "outer_bonus": attrs.outer_bonus,
        "min_mingjin": attrs.min_mingjin, "max_mingjin": attrs.max_mingjin,
        "mingjin_pen": attrs.mingjin_pen,
        "mingjin_bonus": attrs.mingjin_bonus,
        "min_lieshi": attrs.min_lieshi, "max_lieshi": attrs.max_lieshi,
        "lieshi_pen": attrs.lieshi_pen,
        "lieshi_bonus": attrs.lieshi_bonus,
        "min_qiansi": attrs.min_qiansi, "max_qiansi": attrs.max_qiansi,
        "qiansi_pen": attrs.qiansi_pen,
        "qiansi_bonus": attrs.qiansi_bonus,
        "min_pozhu": attrs.min_pozhu, "max_pozhu": attrs.max_pozhu,
        "pozhu_pen": attrs.pozhua_pen,
        "pozhu_bonus": attrs.pozhua_bonus,
        "min_wuxiang": attrs.min_wuxiang, "max_wuxiang": attrs.max_wuxiang,
        "precision": attrs.precision,
        "crit_rate": attrs.crit_rate,
        "intent_rate": attrs.intent_rate,
        "direct_crit": attrs.direct_crit, "direct_intent": attrs.direct_intent,
        "crit_dmg": attrs.crit_dmg, "intent_dmg": attrs.intent_dmg,
        "all_skill_bonus": attrs.all_skill_bonus,
        "boss_bonus": attrs.boss_bonus,
        "single_qs_bonus": attrs.single_qs_bonus,
        "group_qs_bonus": attrs.group_qs_bonus,
    }
    for key in ("weapon_bonus_primary", "weapon_bonus_secondary", "special_bonus"):
        spec = model.get("inputs", {}).get(key)
        if spec is None:
            continue
        affix_names = spec.get("affix_names")
        if not isinstance(affix_names, list):
            raise ValueError(f"graduation input {key} has no standard affix names")
        values[key] = sum(attrs.extra_attrs.get(name, 0.0) for name in affix_names)
    return values


def invalidate_graduation_cache() -> None:
    """清除 Excel 模型 JSON 缓存（覆写方案后调用以确保重新加载）。"""
    GenericCalculator._load_data.cache_clear()


def get_graduation_calculator(
    school_name: str, scheme_name: str = "基础方案",
) -> GraduationCalculator | None:
    if school_name not in _ALL_SCHOOLS or not scheme_name:
        return None
    try:
        return GenericCalculator(school_name, scheme_name)
    except Exception as exc:
        logger.error(f"创建毕业率计算器失败 ({school_name}/{scheme_name}): {exc}")
        return None
