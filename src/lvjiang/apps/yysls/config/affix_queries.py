"""词条可用性、上限与品阶查询；读取统一配置快照。"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from .affix_levels import OPEN as OPEN_LEVEL_RANGE
from .affix_levels import LevelRange
from .constants import (
    _KEY_TO_TYPE,
    _TYPE_TO_KEY,
    AFFIX_CATEGORY_NAMES,
    EQUIP_PART_NAMES,
    POOL_DINGYIN,
    POOL_NORMAL,
    STACK_SUM,
)
from .models import (
    AffixUpgrade,
)

if TYPE_CHECKING:
    from .manager import GameConfigManager

def base_attr_names(self: GameConfigManager) -> list[str]:
    """所有合法基础属性名（从 game_config/equipment.yaml 的 _attr 字段收集）

    如 ['外功攻击', '最小外功攻击', '最大外功攻击', '气血最大值']
    """
    return list(self._base_attr_names)


def resolve_affix_category(self: GameConfigManager, affix_name: str) -> str:
    """真实词条名 → 配置类别名

    无映射则原样返回。
    映射关系从 YAML 的 _aliases 字段加载。
    """
    return self._alias_to_category.get(affix_name, affix_name)


def get_aliases_for_category(self: GameConfigManager, category: str) -> list[str]:
    """获取某类别下的所有别名（分组类别返回全部组内词条）"""
    return [alias for alias, cat in self._alias_to_category.items() if cat == category]


def get_alias_groups(self: GameConfigManager, category: str) -> dict[str, list[str]]:
    """获取某类别的词条分组（组名 → 词条名列表）

    未分组的类别返回空 dict。
    """
    return self._alias_groups.get(category, {})


def get_affix_categories(self: GameConfigManager) -> dict[str, list[str]]:
    """词条归属映射（按 AFFIX_CATEGORY_NAMES 顺序：归属名 → 词条名列表副本）"""
    return {cat: list(self._affix_categories.get(cat, []))
            for cat in AFFIX_CATEGORY_NAMES}


def get_affix_category(self: GameConfigManager, affix_name: str) -> str:
    """词条名 → 归属名（无归属返回空串）"""
    return self._affix_to_category.get(affix_name, "")


def get_affix_parts(self: GameConfigManager, affix_name: str) -> list[str]:
    """词条可出现的装备部位

    优先级：单独配置(affix_parts) > 分类级(_parts) > 全部七部位。
    """
    individual = self._affix_parts.get(affix_name)
    if individual:
        return list(individual)
    # 回退到分类级 _parts（使用 _alias_to_category，包含定音词条）
    category = self._alias_to_category.get(affix_name, "")
    if category:
        cat_parts = self._category_parts.get(category)
        if cat_parts:
            return list(cat_parts)
    return list(EQUIP_PART_NAMES)


def get_affix_aliases(self: GameConfigManager, affix_name: str) -> list[str]:
    """返回精准词条配置的外部简称。"""
    return list(self._affix_external_aliases.get(affix_name) or [])


def get_alias_groups_for_category(self: GameConfigManager, category: str) -> dict[str, list[str]]:
    """返回词条类别的标准名称分组快照。"""
    return copy.deepcopy(self._alias_groups.get(category) or {})


def get_affix_names_for_alias(self: GameConfigManager, alias: str) -> list[str]:
    """严格按外部简称返回精准词条名，不执行任何模糊匹配。"""
    return list(self._external_alias_to_affixes.get(alias) or [])


def get_affix_stack(self: GameConfigManager, affix_name: str) -> str:
    """词条同名叠加方式：``sum``（累加，缺省）或 ``max``（全套只取最高一条）。

    按词条所属词组的 ``_stack`` 字段查询；未知词条按累加处理。
    """
    category = self._alias_to_category.get(affix_name)
    if not category:
        return STACK_SUM
    return self._affix_stacks.get(category, STACK_SUM)


def get_affix_pool(self: GameConfigManager, affix_name: str) -> str:
    """查询词条的词库类型（自动映射到类别）

    Returns:
        POOL_NORMAL（普通词条，缺省）或 POOL_DINGYIN（定音词条）
    """
    category = self.resolve_affix_category(affix_name)
    return self._affix_pools.get(category, POOL_NORMAL)


def is_dingyin_affix(self: GameConfigManager, affix_name: str) -> bool:
    """是否定音词条"""
    return self.get_affix_pool(affix_name) == POOL_DINGYIN


def get_normal_affix_names(self: GameConfigManager) -> list[str]:
    """全部普通词条标准名（非定音词组的 _aliases 并集，按 YAML 声明序）

    调律规则校验与调律规则 UI 词条候选的唯一来源。
    """
    return [alias for alias, cat in self._alias_to_category.items()
            if self._affix_pools.get(cat, POOL_NORMAL) != POOL_DINGYIN]


def get_dingyin_affix_names(self: GameConfigManager) -> list[str]:
    """全部定音词条标准名（定音词组的 _aliases 并集，按 YAML 声明序）"""
    return [alias for alias, cat in self._alias_to_category.items()
            if self._affix_pools.get(cat, POOL_NORMAL) == POOL_DINGYIN]


def get_affix_caps(self: GameConfigManager, level: int, affix_name: str) -> dict | None:
    """查询某等级某词条的上限

    Args:
        level: 装备等级
        affix_name: 真实词条名（自动映射到类别）

    Returns:
        {"cap": float, "unit": str, "chengyin": float} 或 None

    ``cap`` 读取该等级配置；普通词条的 ``chengyin`` 优先使用显式值，
    缺省时在加载阶段按游戏固定 94% 比例计算。定音词条不受承音限制，
    承音值 = cap。
    unit 从词组级别的 _unit 字段读取。
    """
    category = self.resolve_affix_category(affix_name)
    caps = self._affix_caps.get(category, {})
    entry = caps.get(level)
    if entry is None:
        return None
    cap = entry["cap"]
    if self._affix_pools.get(category) == POOL_DINGYIN:
        chengyin = cap
    else:
        chengyin = entry["chengyin"]
    return {
        "cap": cap,
        "unit": self._affix_units.get(category, ""),
        "chengyin": chengyin,
    }


def get_affix_cap_levels(self: GameConfigManager, affix_name: str) -> list[int]:
    """返回词条已登记数值的等级，供跨等级规则选择相邻档位。"""
    category = self.resolve_affix_category(affix_name)
    return sorted(self._affix_caps.get(category, {}))


def get_all_affix_categories(self: GameConfigManager) -> list[str]:
    """返回所有已配置的词条类别名"""
    return list(self._affix_caps.keys())


def get_base_attr_values(
    self: GameConfigManager, part: str, level: int, quality: str
) -> tuple[int | None, int | None]:
    """查询装备部位的基础属性值

    Args:
        part: 部位 key（weapon/ring/pendant/head/chest/leg/wrist）
        level: 装备等级
        quality: 品阶（gold/purple/blue）

    Returns:
        (min_val, max_val): 区间属性返回不同值，点值属性 min==max
        未找到返回 (None, None)

    用途：
    - weapon: 返回外功攻击区间 (min_outer, max_outer)
    - ring: 返回 (最小外功攻击, 最小外功攻击)
    - pendant: 返回 (最大外功攻击, 最大外功攻击)
    - head/chest/leg/wrist: 返回 (气血, 气血)
    """
    rule = self._base_rules.get(part, {}).get(level)
    if rule is None:
        return None, None
    for r in rule.ranges:
        if r.quality == quality:
            return r.min_val, r.max_val
    return None, None


def get_first_affixes(self: GameConfigManager, part: str, level: int | None = None
                      ) -> list[str]:
    """指定部位的首词条候选列表（来自 base_attrs.<part>._first_affixes）

    part 为 group_key（weapon/ring/pendant/head/chest/leg/wrist）。
    未配置时返回空列表。

    给了 ``level`` 就按该等级的生效范围过滤。这里的等级应当是装备的
    **原生等级**：首词条在装备产出时就定死了，承音升阶不会换掉它，拿
    当前等级去判会把「原生 110 首出、已承音到 115」的装备误判成非法。
    """
    names = self._first_affixes.get(part, [])
    if level is None:
        return list(names)
    ranges = self._first_affix_levels.get(part, {})
    return [name for name in names
            if ranges.get(name, OPEN_LEVEL_RANGE).covers(level)]


def get_first_affix_level_range(self: GameConfigManager, part: str,
                                affix_name: str) -> LevelRange:
    """该部位该首词条的生效等级范围；未配置为开区间。"""
    return self._first_affix_levels.get(part, {}).get(
        affix_name, OPEN_LEVEL_RANGE)


def get_affix_level_range(self: GameConfigManager, affix_name: str) -> LevelRange:
    """词条自身的生效等级范围；未配置为开区间。"""
    return self._affix_levels.get(affix_name, OPEN_LEVEL_RANGE)


def is_affix_available(self: GameConfigManager, affix_name: str,
                       level: int | None) -> bool:
    """该等级能否**新产出**这个词条（调律、转律目标等）。

    只管产出，不管合法性：退役词条在旧装备上仍然合法、仍要能被 OCR
    识别和保存，那些走词条注册表，不走这里。
    """
    return self.get_affix_level_range(affix_name).covers(level)


def get_affix_upgrades(self: GameConfigManager) -> list[AffixUpgrade]:
    """全部跨等级词条升级规则（按配置声明序）。"""
    return list(self._affix_upgrades)


def resolve_affix_upgrade(self: GameConfigManager, affix_name: str, from_level: int,
                          to_level: int,
                          operation: str = "chengyin") -> str:
    """装备从 ``from_level`` 升到 ``to_level`` 时该词条变成什么。

    没有匹配规则返回空串（调用方保持原词条不变）。规则可以串联：一次
    跨过两道坎时逐道应用，避免为「105 直接到 120」单独写规则。
    """
    name = affix_name
    changed = True
    while changed:
        changed = False
        for rule in self._affix_upgrades:
            if (rule.operation == operation
                    and rule.applies(name, from_level, to_level)):
                name = rule.to_name
                changed = True
                break
    return "" if name == affix_name else name


def get_affix_names_in_category(self: GameConfigManager, category: str) -> list[str]:
    """某词条类别下的全部具体词条名（含分组的取并集）。"""
    names: list[str] = []
    for group in (self._alias_groups.get(category) or {}).values():
        names.extend(group)
    if not names:
        names = [a for a, c in self._alias_to_category.items()
                 if c == category]
    return sorted(dict.fromkeys(names))


def get_affix_names_in_group(self: GameConfigManager, category: str, group: str) -> list[str]:
    """返回词组类别中指定分组的精准词条名。

    ``指定技能增效`` 以流派名分组。这里直接读取结构化分组，不能再靠
    武学名前缀猜测：醉拳等词条会使用技能体系名，未必以武学名开头。
    """
    names = (self._alias_groups.get(str(category or ""), {})
             .get(str(group or ""), []))
    return list(dict.fromkeys(names))


def get_affix_category_parts(self: GameConfigManager, category: str) -> list[str]:
    """词条类别声明的适用部位（affix_caps 内的 _parts）。

    攻具/防具的划分直接用它，不另建分组：`指定技能增效` 的 _parts 就是
    防具四件，粒度比全局二分更准，而且加部位时只改配置。
    """
    return list(self._category_parts.get(str(category or ""), []))


def infer_quality(self: GameConfigManager, equip_type: str | None, level: int,
                  value: int | list | tuple) -> str | None:
    """根据基础属性推断品阶

    Args:
        equip_type: 装备类型（剑/枪/环/佩/冠胄/胸甲/...），可为 None
        level: 装备等级
        value: 属性值——区间属性（武器）为 [min, max]，需两端精确匹配；
               点值属性（首饰/防具）为标量

    Returns:
        'gold' / 'purple' / 'blue' / None（无匹配）

    策略：
        1. 若 equip_type 已知，先精确查找对应类别
        2. 若类型未知或精确查找失败，遍历所有类别贪婪匹配
           （防具气血值在不同类别间不重叠，可唯一确定）
    """
    # 策略 1：精确查找
    if equip_type:
        key = _TYPE_TO_KEY.get(equip_type)
        if key:
            rule = self._base_rules.get(key, {}).get(level)
            if rule:
                result = rule.infer_quality(value)
                if result:
                    return result

    # 策略 2：贪婪匹配（遍历所有类别）
    for _key, level_rules in self._base_rules.items():
        rule = level_rules.get(level)
        if rule:
            result = rule.infer_quality(value)
            if result:
                return result
    return None


def infer_level_quality(
    self: GameConfigManager, equip_type: str | None, value: int | list | tuple
) -> tuple[int | None, str | None]:
    """仅凭基础属性值反查 (等级, 品阶)

    基础属性值全局唯一（跨等级、跨品阶、跨部位均不重复），
    故 OCR 漏识别等级时，仍可仅凭数值反查出等级与品阶。

    Args:
        equip_type: 装备类型，可为 None
        value: 属性值——区间属性为 [min, max]，点值属性为标量

    Returns:
        (level, quality)；无匹配返回 (None, None)

    策略：
        1. 若 equip_type 已知，先在其对应类别内遍历全部等级
        2. 否则/失败则遍历所有类别 × 所有等级贪婪匹配
    """
    # 策略 1：type 已知，只在其类别内遍历所有等级
    if equip_type:
        key = _TYPE_TO_KEY.get(equip_type)
        if key:
            for level, rule in self._base_rules.get(key, {}).items():
                quality = rule.infer_quality(value)
                if quality:
                    return level, quality

    # 策略 2：遍历所有类别 × 所有等级
    for level_rules in self._base_rules.values():
        for level, rule in level_rules.items():
            quality = rule.infer_quality(value)
            if quality:
                return level, quality
    return None, None


def infer_type_by_value(self: GameConfigManager, value: int | list | tuple) -> str | None:
    """仅凭基础属性值反查装备部位 type（equip_type OCR 缺失时回填用）

    遍历全部类别收集命中部位，仅唯一命中时返回：
    - 胸甲/环/佩 数值独立，可唯一确定；
    - 冠胄/胫甲/腕甲 数值相同（_follow），命中多个部位 → None；
    - 武器 key 对应多种武器类型，不参与反查。

    Returns:
        部位 type（如 "胸甲"）；无命中或命中多个部位返回 None
    """
    matched: set[str] = set()
    for key, level_rules in self._base_rules.items():
        part_type = _KEY_TO_TYPE.get(key)
        if part_type is None:
            continue    # 武器类别不参与
        for rule in level_rules.values():
            if rule.infer_quality(value):
                matched.add(part_type)
                break
    if len(matched) == 1:
        return matched.pop()
    return None
