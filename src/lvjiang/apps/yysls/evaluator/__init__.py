"""装备判定模块

提供穷举匹配制的四档评级（顶级/优秀/一般/垃圾）与规则驱动的
通用调律规则判定器（GenericTuningJudge + tuning_rules YAML）。
"""

from .base import JudgeResult, Rating, TuningJudge
from .judge import GenericTuningJudge
from .registry import (
    get_rule_names,
    get_tuning_judge,
    get_tuning_rules,
    is_rule_implemented,
    judge_equipment_potential,
    judge_tuning_worthiness,
    summarize_potential,
)
from .tuning_rules import (
    TuningRule,
    TuningRuleManager,
    get_tuning_rule_manager,
)

__all__ = [
    "Rating",
    "JudgeResult",
    "TuningJudge",
    "GenericTuningJudge",
    "TuningRule",
    "TuningRuleManager",
    "get_tuning_rule_manager",
    "get_tuning_rules",
    "get_rule_names",
    "get_tuning_judge",
    "is_rule_implemented",
    "judge_equipment_potential",
    "judge_tuning_worthiness",
    "summarize_potential",
]
