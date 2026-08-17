"""Excel-model-backed DPS and graduation-rate calculator."""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from loguru import logger

from ..combat.combat_attrs import (
    CombatAttributes,
)
from ...config.graduation_session import (
    get_baseline_dps as _get_session_baseline,
    set_baseline_dps as _set_session_baseline,
)
from .graduation_program import ProgramRuntime

_DATA_DIR = (
    Path(__file__).parents[6] / "config" / "system" / "yysls" / "graduation"
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
        if self._data.get("schema_version") != 2:
            raise ValueError(f"unsupported graduation model for {school_name}")
        json_baseline = float(self._data["graduation_baseline_dps"])
        session_override = _get_session_baseline(school_name, scheme_name)
        self._baseline = session_override if session_override is not None else json_baseline
        if self._baseline <= 0:
            raise ValueError("100%毕业率基准 DPS 必须大于 0")
        self._combat_time = float(self._data["environment"]["combat_time"])

    @staticmethod
    @lru_cache(maxsize=len(_ALL_SCHOOLS))
    def _load_data(school_name: str, scheme_name: str) -> dict[str, Any]:
        path = _DATA_DIR / f"{school_name}_{scheme_name}.json"
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)

    def baseline_dps(self) -> float:
        return self._baseline

    def combat_time(self) -> float:
        return self._combat_time

    def calculate(self, attrs: CombatAttributes) -> GraduationResult:
        values = [
            float(getattr(attrs, spec["name"], 0.0))
            if spec["kind"] == "field"
            else float(attrs.extra_attrs.get(spec["name"], 0.0))
            for spec in self._data["program"]["inputs"]
        ]
        outputs = ProgramRuntime(self._data["program"], values).outputs()
        graduation_rate = outputs["dps"] / self._baseline
        return GraduationResult(
            total_damage=outputs["total_damage"],
            dps=outputs["dps"],
            graduation_rate=graduation_rate,
            baseline_dps=self._baseline,
            combat_time=outputs["combat_time"],
        )


def invalidate_graduation_cache() -> None:
    """清除 Excel 模型 JSON 缓存（覆写方案后调用以确保重新加载）。"""
    GenericCalculator._load_data.cache_clear()


def get_graduation_scheme_inputs(
    school_name: str, scheme_name: str,
) -> list[dict[str, Any]]:
    """返回方案的 Excel 输入满值；食物加成不属于输入契约，因此不会返回。"""
    model = GenericCalculator._load_data(school_name, scheme_name)
    attrs = CombatAttributes.from_dict(model["baseline_attrs"])
    result: list[dict[str, Any]] = []
    for spec in model["program"]["inputs"]:
        name = spec["name"]
        value = getattr(attrs, name, 0.0) if spec["kind"] == "field" else attrs.extra_attrs.get(name, 0.0)
        result.append({
            "name": name,
            "value": float(value),
            "kind": spec["kind"],
        })
    return result


def get_graduation_scheme_combat_attrs(
    school_name: str, scheme_name: str,
) -> CombatAttributes:
    """将方案满值输入转换成战斗属性面板的标准数据模型。"""
    model = GenericCalculator._load_data(school_name, scheme_name)
    if model.get("schema_version") != 2:
        raise ValueError("方案不是当前 v2 格式，请重新导入 Excel")
    return CombatAttributes.from_dict(model["baseline_attrs"])


def get_graduation_scheme_metrics(
    school_name: str, scheme_name: str,
) -> tuple[float, float]:
    """返回方案满值 ADPS 与可校正的 100% 毕业率基准 DPS（含 session 覆盖）。"""
    model = GenericCalculator._load_data(school_name, scheme_name)
    session_override = _get_session_baseline(school_name, scheme_name)
    baseline = session_override if session_override is not None else float(model["graduation_baseline_dps"])
    return (
        float(model["reference"]["dps"]),
        baseline,
    )


def set_graduation_baseline_dps(
    school_name: str, scheme_name: str, value: float,
) -> None:
    """将方案的 100% 毕业率基准 DPS 写入 session 覆盖层。"""
    model = GenericCalculator._load_data(school_name, scheme_name)
    if model.get("schema_version") != 2:
        raise ValueError("方案不是当前 v2 格式，请重新导入 Excel")
    _set_session_baseline(school_name, scheme_name, value)
    invalidate_graduation_cache()


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
