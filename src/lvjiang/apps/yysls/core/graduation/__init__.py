"""Excel-model-backed DPS and graduation-rate calculator."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from loguru import logger

from ...config.graduation_session import (
    get_baseline_dps as _get_session_baseline,
)
from ...config.graduation_session import (
    set_baseline_dps as _set_session_baseline,
)
from ..combat.combat_attrs import (
    CombatAttributes,
)
from .graduation_program import ProgramRuntime
from .model_inputs import adapt_attrs_to_model_inputs
from .model_registry import (
    GraduationModelRef,
    invalidate_model_registry,
    load_model,
    select_graduation_model,
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

    def __init__(self, model: GraduationModelRef) -> None:
        self.model = model
        self._school = model.school
        self._scheme = model.scheme
        self._data = load_model(model.rel_path)
        json_baseline = float(self._data["graduation_baseline_dps"])
        session_override = _get_session_baseline(
            model.school, model.scheme, model.level, model.version)
        self._baseline = session_override if session_override is not None else json_baseline
        if self._baseline <= 0:
            raise ValueError("100%毕业率基准 DPS 必须大于 0")
        self._combat_time = float(self._data["environment"]["combat_time"])

    def baseline_dps(self) -> float:
        return self._baseline

    def combat_time(self) -> float:
        return self._combat_time

    def calculate(self, attrs: CombatAttributes) -> GraduationResult:
        specs = self._data["program"]["inputs"]
        attrs = adapt_attrs_to_model_inputs(attrs, specs)
        values = [
            float(getattr(attrs, spec["name"], 0.0))
            if spec["kind"] == "field"
            else float(attrs.extra_attrs.get(spec["name"], 0.0))
            for spec in specs
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


def _fold_all_qs_bonus(
    attrs: CombatAttributes, specs: list[dict[str, Any]],
) -> CombatAttributes:
    """兼容原内部入口；实际契约集中在 model_inputs。"""
    return adapt_attrs_to_model_inputs(attrs, specs)


def invalidate_graduation_cache() -> None:
    """清除 Excel 模型 JSON 缓存（覆写方案后调用以确保重新加载）。"""
    invalidate_model_registry()


def _effective_world_level(world_level: int | None) -> int:
    if world_level is not None:
        return int(world_level)
    from ...config import get_game_config
    return get_game_config().current_equip_level()


def _selected_model(
    school_name: str, scheme_name: str, world_level: int | None,
) -> GraduationModelRef:
    level = _effective_world_level(world_level)
    model = select_graduation_model(school_name, scheme_name, level)
    if model is None:
        raise FileNotFoundError(
            f"流派「{school_name}」方案「{scheme_name}」没有适用于个人世界等级 "
            f"{level} 的毕业率模型")
    return model


def get_graduation_scheme_inputs(
    school_name: str, scheme_name: str, world_level: int | None = None,
) -> list[dict[str, Any]]:
    """返回方案的 Excel 输入满值；食物加成不属于输入契约，因此不会返回。"""
    model = load_model(
        _selected_model(school_name, scheme_name, world_level).rel_path)
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
    school_name: str, scheme_name: str, world_level: int | None = None,
    *, model_ref: GraduationModelRef | None = None,
) -> CombatAttributes:
    """将方案满值输入转换成战斗属性面板的标准数据模型。"""
    model = load_model((
        model_ref
        or _selected_model(school_name, scheme_name, world_level)
    ).rel_path)
    return CombatAttributes.from_dict(model["baseline_attrs"])


def get_graduation_scheme_metrics(
    school_name: str, scheme_name: str, world_level: int | None = None,
    *, model_ref: GraduationModelRef | None = None,
) -> tuple[float, float]:
    """返回方案满值 ADPS 与可校正的 100% 毕业率基准 DPS（含 session 覆盖）。"""
    ref = model_ref or _selected_model(school_name, scheme_name, world_level)
    model = load_model(ref.rel_path)
    session_override = _get_session_baseline(
        school_name, scheme_name, ref.level, ref.version)
    baseline = session_override if session_override is not None else float(model["graduation_baseline_dps"])
    return (
        float(model["reference"]["dps"]),
        baseline,
    )


def set_graduation_baseline_dps(
    school_name: str, scheme_name: str, value: float,
    world_level: int | None = None,
    *, model_ref: GraduationModelRef | None = None,
) -> None:
    """将方案的 100% 毕业率基准 DPS 写入 session 覆盖层。"""
    ref = model_ref or _selected_model(school_name, scheme_name, world_level)
    _set_session_baseline(
        school_name, scheme_name, ref.level, ref.version, value)
    invalidate_graduation_cache()


def get_graduation_calculator(
    school_name: str, scheme_name: str = "基础方案",
    world_level: int | None = None,
) -> GraduationCalculator | None:
    if school_name not in _ALL_SCHOOLS or not scheme_name:
        return None
    try:
        return GenericCalculator(
            _selected_model(school_name, scheme_name, world_level))
    except Exception as exc:
        logger.error(
            f"创建毕业率计算器失败 ({school_name}/{scheme_name}/"
            f"世界等级 {_effective_world_level(world_level)}): {exc}")
        return None
