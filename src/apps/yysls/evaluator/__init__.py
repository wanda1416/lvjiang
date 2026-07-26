"""装备判定模块

提供穷举匹配制的四档评级（顶级/优秀/能用/垃圾）、规则驱动的
通用流派判定器（GenericSchoolJudge + tuning_rules YAML）
及词条规则管理（AttrRuleManager）。
"""

from .base import JudgeResult, Rating, SchoolJudge
from .generic import GenericSchoolJudge
from .rules import (
    SchoolRule, TuningRuleManager, get_tuning_rule_manager,
)
from .schools import (
    get_school_judge, get_school_rules, get_schools,
    is_school_implemented, judge_tuning_worthiness,
)
from .attr_rules import get_attr_rule_manager

__all__ = [
    "Rating",
    "JudgeResult",
    "SchoolJudge",
    "GenericSchoolJudge",
    "SchoolRule",
    "TuningRuleManager",
    "get_tuning_rule_manager",
    "get_school_rules",
    "get_schools",
    "get_school_judge",
    "is_school_implemented",
    "judge_tuning_worthiness",
    "get_attr_rule_manager",
]
