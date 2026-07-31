"""调律规则加载与管理

规则全部外置为 YAML（config/system/yysls/tuning_rules/ 下每规则一个
文件），本包负责加载、schema 校验、缓存、创建/删除与保存。判定
逻辑见 generic.GenericTuningJudge，规则变更零代码改动。

规则中的全部词条引用一律使用规则词条候选集（标准词条全集
加入四个动态词条，见 models.rule_affix_candidates()），
校验失败即保存拒绝，消除符号二次映射与静默失配。

分层：models（领域模型 + 固定词汇）← parsing（YAML 解析校验）←
manager（加载缓存 + 持久化 + 单例），外部一律经本 __init__ 导入。
"""

from .manager import (
    TuningBaseManager,
    TuningRuleManager,
    get_tuning_base,
    get_tuning_base_manager,
    get_tuning_rule_manager,
)
from .models import (
    ATTR_ATTACK_CATEGORY,
    BEHAVIOR_ACTION_LABELS,
    BEHAVIOR_ACTIONS,
    BEHAVIOR_STAGE_ACTIONS,
    BEHAVIOR_STAGE_LABELS,
    COND_KINDS,
    DYNAMIC_AFFIXES,
    DYNAMIC_CATEGORY,
    FOOD_EXPECT_KEYS,
    FOOD_LABELS,
    GENERIC_ATTR,
    INSUFFICIENT_ACTIONS,
    INSUFFICIENT_LABELS,
    JUDGE_SCOPE_LABELS,
    JUDGE_SCOPES,
    MAX_TUNE_RESETS,
    PART_ALIAS,
    PART_KEYS,
    QUALITY_LABELS,
    QUALITY_PARTS,
    QUALITY_RANK,
    RATING_KEYS,
    RATING_LABELS,
    RATING_RANK,
    STONE_LABEL,
    TIER_KEYS,
    BehaviorRule,
    BehaviorSettings,
    CommonConditions,
    Condition,
    ConditionGroup,
    FoodDecision,
    FoodRule,
    MaterialSettings,
    PartPattern,
    Playstyle,
    RatingProvider,
    RuleValidationError,
    ScanBehavior,
    TuneBehavior,
    TuningBase,
    TuningRule,
    WeaponSide,
    default_food_rules,
    dynamic_affix_map,
    rule_affix_candidates,
    specific_attr_names,
    standard_affix_names,
    standard_playstyle_attrs,
)
from .parsing import parse_tuning_base, parse_tuning_rule

__all__ = [
    # 固定词汇
    "ATTR_ATTACK_CATEGORY",
    "BEHAVIOR_ACTION_LABELS",
    "BEHAVIOR_ACTIONS",
    "BEHAVIOR_STAGE_ACTIONS",
    "BEHAVIOR_STAGE_LABELS",
    "COND_KINDS",
    "DYNAMIC_AFFIXES",
    "DYNAMIC_CATEGORY",
    "FOOD_EXPECT_KEYS",
    "FOOD_LABELS",
    "GENERIC_ATTR",
    "INSUFFICIENT_ACTIONS",
    "INSUFFICIENT_LABELS",
    "JUDGE_SCOPE_LABELS",
    "JUDGE_SCOPES",
    "MAX_TUNE_RESETS",
    "PART_ALIAS",
    "PART_KEYS",
    "QUALITY_LABELS",
    "QUALITY_PARTS",
    "QUALITY_RANK",
    "RATING_KEYS",
    "RATING_LABELS",
    "RATING_RANK",
    "STONE_LABEL",
    "TIER_KEYS",
    # 领域模型
    "BehaviorRule",
    "BehaviorSettings",
    "CommonConditions",
    "Condition",
    "ConditionGroup",
    "FoodDecision",
    "FoodRule",
    "MaterialSettings",
    "PartPattern",
    "Playstyle",
    "RatingProvider",
    "ScanBehavior",
    "TuneBehavior",
    "TuningBase",
    "TuningRule",
    "WeaponSide",
    "RuleValidationError",
    # 词汇/等价工具
    "default_food_rules",
    "dynamic_affix_map",
    "rule_affix_candidates",
    "specific_attr_names",
    "standard_affix_names",
    "standard_playstyle_attrs",
    # 解析
    "parse_tuning_base",
    "parse_tuning_rule",
    # 管理器与单例
    "TuningBaseManager",
    "TuningRuleManager",
    "get_tuning_base",
    "get_tuning_base_manager",
    "get_tuning_rule_manager",
]
