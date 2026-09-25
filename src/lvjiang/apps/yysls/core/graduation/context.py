"""备战方案 → 毕业率计算上下文，只构造一次、各入口共用。

从方案取“用什么算”：主副武学 → 流派；流派 + 方案名 → 毕业率计算器；
玩法基础属性 + 弓玦 → 基础属性；再带上玩法名（满定音要用）。弓玦固定为
方案已选的那一套——智能调律不假设用户会为一件装备换弓玦。
"""
from __future__ import annotations

from dataclasses import dataclass

from ...config.play_styles import get_play_styles
from ..combat.combat_attrs import (
    CombatAttributes,
    GraduationAttrContext,
    compute_gongjue_attrs,
)
from ..loadout.models import LoadoutPlan, resolve_school
from .scoring import LoadoutScorer


class PlanContextError(ValueError):
    """方案缺少算毕业率所需的信息；``reason`` 是给用户看的一句话。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def gongjue_attrs(
    gongjue: str, game_config=None, *, world_level: int | None = None,
) -> CombatAttributes:
    """弓玦套装属性：个人世界等级三率词条上限的一半；空套装为零。"""
    if not gongjue:
        return CombatAttributes()
    if game_config is None:
        from ...config import get_game_config
        game_config = get_game_config()
    level = (
        int(world_level) if world_level is not None
        else game_config.current_equip_level()
    )
    if not level:
        return CombatAttributes()
    return compute_gongjue_attrs(gongjue, level, game_config.get_affix_caps)


@dataclass(frozen=True)
class PlanScoringContext:
    plan_id: str
    plan_name: str
    school: str
    scheme: str
    world_level: int
    model_level: int
    model_version: int
    calculator: object
    attr_context: GraduationAttrContext
    base_attrs: CombatAttributes      # 含弓玦
    #: 不含弓玦的基础属性；只给最优组合按弓玦场景自行叠加用
    base_attrs_without_gongjue: CombatAttributes
    gongjue: str
    playstyle: str
    attribute: str                    # 流派属性（鸣金/裂石/…），动态词条归类用

    @classmethod
    def from_plan(
        cls,
        plan: LoadoutPlan,
        *,
        game_config=None,
        schools: dict | None = None,
        world_level: int | None = None,
    ) -> PlanScoringContext:
        from . import get_graduation_calculator

        if game_config is None:
            from ...config import get_game_config
            game_config = get_game_config()
        if schools is None:
            schools = game_config.get_schools()
        effective_level = (
            int(world_level) if world_level is not None
            else game_config.current_equip_level()
        )
        school = resolve_school(
            plan.main_martial_art, plan.sub_martial_art, schools)
        if not school:
            raise PlanContextError("当前备战方案的主副武学无法匹配流派，请检查武学选择")
        problems: list[str] = []
        calculator = None
        if not plan.graduation_scheme:
            problems.append("当前备战方案未选择毕业率方案")
        else:
            calculator = get_graduation_calculator(
                school, plan.graduation_scheme, effective_level)
            if calculator is None:
                from .model_registry import describe_missing_model
                problems.append(describe_missing_model(
                    school, plan.graduation_scheme, effective_level))
        base_data = None
        if not plan.base_attribute:
            problems.append("当前备战方案未选择角色基础属性")
        else:
            base_data = get_play_styles(school).get(plan.base_attribute)
            if not isinstance(base_data, dict):
                problems.append(f"角色基础属性「{plan.base_attribute}」在流派「{school}」下不存在")
        if problems:
            raise PlanContextError("；".join(problems))
        assert calculator is not None and isinstance(base_data, dict)
        raw_base = CombatAttributes.from_dict(base_data)
        base_attrs = raw_base + gongjue_attrs(
            plan.gongjue, game_config, world_level=effective_level)
        attr_context = GraduationAttrContext.from_school(
            school, world_level=effective_level, game_config=game_config)
        model = calculator.model
        return cls(
            plan_id=plan.id,
            plan_name=plan.name,
            school=school,
            scheme=plan.graduation_scheme,
            world_level=effective_level,
            model_level=model.level,
            model_version=model.version,
            calculator=calculator,
            attr_context=attr_context,
            base_attrs=base_attrs,
            base_attrs_without_gongjue=raw_base,
            gongjue=plan.gongjue,
            playstyle=plan.playstyle,
            attribute=str((schools.get(school) or {}).get("attr") or ""),
        )

    def scorer(self, *, game_config=None, **budget) -> LoadoutScorer:
        return LoadoutScorer(
            self.calculator, self.base_attrs, self.school, game_config,
            attr_context=self.attr_context,
            **budget)
