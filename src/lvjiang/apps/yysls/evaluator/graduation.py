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
        special_names = self._data.get("inputs", {}).get(
            "special_bonus", {},
        ).get("affix_names")
        if not isinstance(special_names, list) or len(special_names) > 1:
            raise ValueError(
                f"方案「{school_name}/{scheme_name}」的指定技能增效无法解析为"
                "至多一个精准词条，请修正词组配置或 Excel 后重新导入。"
            )
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
        "pozhu_pen": attrs.pozhu_pen,
        "pozhu_bonus": attrs.pozhu_bonus,
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


def get_graduation_scheme_inputs(
    school_name: str, scheme_name: str,
) -> list[dict[str, Any]]:
    """返回方案的 Excel 输入满值；食物加成不属于输入契约，因此不会返回。"""
    model = GenericCalculator._load_data(school_name, scheme_name)
    result: list[dict[str, Any]] = []
    for name, spec in model.get("inputs", {}).items():
        sheet_name, coordinate = spec["target"].split("!", 1)
        cell = model["sheets"][sheet_name]["cells"].get(coordinate, {})
        value = cell.get("value", cell.get("cached", 0))
        if value is None:
            value = 0
        result.append({
            "name": name,
            "value": float(value),
            "unit": spec.get("unit", "point"),
            "affix_names": list(spec.get("affix_names") or []),
        })
    return result


def get_graduation_scheme_combat_attrs(
    school_name: str, scheme_name: str,
) -> CombatAttributes:
    """将方案满值输入转换成战斗属性面板的标准数据模型。"""
    attrs = CombatAttributes()
    dynamic_inputs = {
        "weapon_bonus_primary", "weapon_bonus_secondary", "special_bonus",
    }
    from ..config import get_game_config

    skill_group = get_game_config().get_alias_groups(
        "指定技能增效",
    ).get(school_name, [])
    for entry in get_graduation_scheme_inputs(school_name, scheme_name):
        name = entry["name"]
        value = float(entry["value"])
        if name in dynamic_inputs:
            # Excel 简称只用于转换时查别名；展示和计算语义只保留精准词条名。
            exact_names = list(entry.get("affix_names") or [])
            if name == "special_bonus":
                exact_names = [
                    exact for exact in skill_group if exact in exact_names
                ]
                if not exact_names:
                    # 空列表是合法语义：该流派不需要指定技能增效。
                    continue
            if not exact_names:
                raise ValueError(
                    f"流派「{school_name}」的方案输入 {name} 没有可用的精准词条名。"
                    "请修正词组配置或重新导入 Excel。"
                )
            attrs.extra_attrs[exact_names[0]] = value
            continue
        field_name = name
        if hasattr(attrs, field_name):
            setattr(attrs, field_name, value)
    return attrs


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
