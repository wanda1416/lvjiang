"""基础属性来源建模

回答「装备之外的战斗属性从哪来」，产出 ``build_graduation_attrs`` 的
``base_attrs``。装备词条依旧走原有的 ``equipment_attrs`` 通道，本包
不改动任何既有模块。

分层：models（领域模型 + 固定词汇）← parsing（YAML 解析校验）←
resolver（两趟求值 + breakdown + 反解）← manager（加载缓存 + 单例），
外部一律经本 __init__ 导入。

双出口：``panel_attrs`` 对得上游戏角色面板（排除吃食一类只在战斗内
生效的加成），``combat_attrs`` 是全集，喂毕业率。两者的差集即
scope=combat 的部分。

未填数值的条目贡献 0 并登记进 ``unmodeled``，缺口由 :func:`solve_residual`
反解成手填补足——所以来源只建了一半也是可用状态。
"""

from .builtin import (
    BUILTIN_PREFIX,
    DIMENSION_JIN,
    DIMENSION_MIN,
    DIMENSION_SHI,
    dimension_effects,
)
from .manager import (
    AttrModelManager,
    game_config_caps_lookup,
    get_attr_model_manager,
    invalidate_attr_model_cache,
)
from .models import (
    AFFIX_RANGE_FIELDS,
    AFFIX_SCALAR_FIELDS,
    ATTR_ATTACK_CATEGORY,
    ATTR_ATTACK_FIELDS,
    DEFAULT_AFFIX_SPLIT,
    DIMENSION_CATEGORY,
    DIMENSION_FIELDS,
    DIMENSION_LABELS,
    PERCENT_FIELDS,
    SCOPE_COMBAT,
    SCOPE_PANEL,
    SCOPES,
    SOURCE_KIND_LABELS,
    SOURCE_KINDS,
    SUPPORTED_FULL_AFFIX_CATEGORIES,
    WORKING_FIELDS,
    AppliedModifier,
    AttrModelError,
    Formula,
    FullAffix,
    ResolveResult,
    ScopeResult,
    StatEffect,
    UnmodeledSource,
    attr_attack_fields,
    split_affix_cap,
)
from .parsing import parse_entry, parse_formula, parse_source_file
from .resolver import (
    RESIDUAL_SOURCE_ID,
    diff_against_panel,
    expand_full_affix,
    resolve,
    solve_residual,
)

__all__ = [
    "AFFIX_RANGE_FIELDS",
    "AFFIX_SCALAR_FIELDS",
    "ATTR_ATTACK_CATEGORY",
    "ATTR_ATTACK_FIELDS",
    "BUILTIN_PREFIX",
    "DEFAULT_AFFIX_SPLIT",
    "DIMENSION_CATEGORY",
    "DIMENSION_FIELDS",
    "DIMENSION_JIN",
    "DIMENSION_LABELS",
    "PERCENT_FIELDS",
    "SUPPORTED_FULL_AFFIX_CATEGORIES",
    "WORKING_FIELDS",
    "DIMENSION_MIN",
    "DIMENSION_SHI",
    "RESIDUAL_SOURCE_ID",
    "SCOPES",
    "SCOPE_COMBAT",
    "SCOPE_PANEL",
    "SOURCE_KINDS",
    "SOURCE_KIND_LABELS",
    "AppliedModifier",
    "AttrModelError",
    "AttrModelManager",
    "Formula",
    "FullAffix",
    "ResolveResult",
    "ScopeResult",
    "StatEffect",
    "UnmodeledSource",
    "attr_attack_fields",
    "diff_against_panel",
    "dimension_effects",
    "expand_full_affix",
    "game_config_caps_lookup",
    "get_attr_model_manager",
    "invalidate_attr_model_cache",
    "parse_entry",
    "parse_formula",
    "parse_source_file",
    "resolve",
    "solve_residual",
    "split_affix_cap",
]
