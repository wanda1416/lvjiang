"""装备判定模块

提供穷举匹配制的四档评级（顶级/优秀/能用/垃圾）、流派判定器
及词条规则管理（AttrRuleManager）。
"""

from .base import JudgeResult, Rating, SchoolJudge
from .schools import (
    SCHOOL_CLASSES, SCHOOLS, SUB_SCHOOL_PLAYSTYLES, SUB_SCHOOLS,
    get_school_judge, is_school_implemented, judge_tuning_worthiness,
)
from .attr_rules import get_attr_rule_manager

__all__ = [
    "Rating",
    "JudgeResult",
    "SchoolJudge",
    "SCHOOLS",
    "SCHOOL_CLASSES",
    "SUB_SCHOOLS",
    "SUB_SCHOOL_PLAYSTYLES",
    "get_school_judge",
    "is_school_implemented",
    "judge_tuning_worthiness",
    "get_attr_rule_manager",
]
