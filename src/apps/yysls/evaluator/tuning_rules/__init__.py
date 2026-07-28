"""调律规则加载与管理

规则全部外置为 YAML（config/system/yysls/tuning_rules/ 下每规则一个
文件），本包负责加载、schema 校验、缓存、创建/删除与保存。判定
逻辑见 generic.GenericTuningJudge，规则变更零代码改动。

规则中的全部词条引用一律使用标准词条名（attributes.yaml 普通词组
_aliases 全集，经 GameConfigManager.get_normal_affix_names() 提供），
校验失败即保存拒绝，消除符号二次映射与静默失配。

分层：models（领域模型 + 固定词汇）← parsing（YAML 解析校验）←
manager（加载缓存 + 持久化 + 单例），外部一律经本 __init__ 导入。
"""

from .models import (
    ATTR_ATTACK_CATEGORY,
    COND_KINDS,
    GENERIC_ATTR,
    PART_ALIAS,
    PART_KEYS,
    PVP_NAMES,
    Condition,
    PartPattern,
    Playstyle,
    PvpPartRule,
    RuleValidationError,
    TuningBase,
    TuningRule,
    WeaponSide,
    attr_equivalence,
    standard_affix_names,
    standard_playstyle_attrs,
)
from .parsing import parse_tuning_base, parse_tuning_rule
from .manager import (
    TuningBaseManager,
    TuningRuleManager,
    get_tuning_base,
    get_tuning_base_manager,
    get_tuning_rule_manager,
)

__all__ = [
    # 固定词汇
    "ATTR_ATTACK_CATEGORY",
    "COND_KINDS",
    "GENERIC_ATTR",
    "PART_ALIAS",
    "PART_KEYS",
    "PVP_NAMES",
    # 领域模型
    "Condition",
    "PartPattern",
    "Playstyle",
    "PvpPartRule",
    "TuningBase",
    "TuningRule",
    "WeaponSide",
    "RuleValidationError",
    # 词汇/等价工具
    "attr_equivalence",
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
