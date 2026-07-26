"""属性规则管理器

统一管理：
1. 基础属性品阶推断（原 EquipAttrConfig）
2. 词条上限查询（含承音值）
3. 真实词条名 → 配置类别名 映射
4. 词库类型查询（普通词条 / 定音词条，YAML 中用 _pool: dingyin 声明）

数据来源：config/system/yysls/attributes.yaml
映射关系通过 YAML 中每个类别的 _aliases 字段声明，支持 UI 动态管理。
_aliases 支持两种形态：list（不分组）或 dict（分组：组名 → 词条名列表，
如 指定技能增效 按十大流派分组）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


# 承音比例
_CHENGYIN_RATIO = 0.94

# 词库类型（_pool 字段取值；缺省为普通词条）
POOL_NORMAL = "normal"
POOL_DINGYIN = "dingyin"


# ─── 品阶推断数据结构 ──────────────────────────────────────

@dataclass
class AttrRange:
    """单个品阶的属性值范围"""
    quality: str           # gold / purple / blue
    min_val: int | None = None
    max_val: int | None = None

    def contains(self, value: int) -> bool:
        if self.min_val is not None and value < self.min_val:
            return False
        if self.max_val is not None and value > self.max_val:
            return False
        return True


@dataclass
class LevelRule:
    """某个分类在某个等级的品阶规则"""
    ranges: list[AttrRange] = field(default_factory=list)

    def infer_quality(self, value: int) -> str | None:
        for r in self.ranges:
            if r.contains(value):
                return r.quality
        return None


# ─── equip_type → 配置 key 映射 ─────────────────────────────

_TYPE_TO_KEY = {
    # 武器类型 → main_weapon（副武器 sub_weapon 默认跟随主武器，
    # 若独立配置则由贪婪匹配兜底）
    "陌刀": "main_weapon", "舞绫鼓": "main_weapon", "双刀": "main_weapon",
    "绳镖": "main_weapon", "唐横刀": "main_weapon", "手甲": "main_weapon",
    "剑": "main_weapon", "枪": "main_weapon", "扇": "main_weapon", "伞": "main_weapon",
    # 首饰
    "环": "ring",
    "佩": "pendant",
    # 防具
    "冠胄": "head", "胫甲": "leg", "腕甲": "wrist",
    "胸甲": "chest",
}

# 八个装备部位（base_attrs 的全部 key，与 UI 展示顺序一致）
BASE_ATTR_PARTS = (
    "main_weapon", "sub_weapon", "ring", "pendant",
    "head", "chest", "leg", "wrist",
)


# ─── 属性规则管理器 ─────────────────────────────────────────

class AttrRuleManager:
    """属性规则管理器

    从 attributes.yaml 加载全部规则，提供：
    - 品阶推断（base_attrs）
    - 词条上限查询（affix_caps）
    - 词条名映射（真实词条 → 配置类别）
    """

    def __init__(self, path: str | Path | None = None):
        if path is None:
            from src.constants import PROJECT_ROOT
            path = PROJECT_ROOT / "config" / "system" / "yysls" / "attributes.yaml"
        self._path = Path(path)

        # 品阶推断：key → level → LevelRule
        self._base_rules: dict[str, dict[int, LevelRule]] = {}
        # 词条上限：category → level → { cap }
        self._affix_caps: dict[str, dict[int, dict]] = {}
        # 词组单位：category → unit（"" 或 "%"）
        self._affix_units: dict[str, str] = {}
        # 词库类型：category → POOL_NORMAL / POOL_DINGYIN
        self._affix_pools: dict[str, str] = {}
        # 词条映射：alias → category
        self._alias_to_category: dict[str, str] = {}
        # 词条分组：category → { 组名 → [词条名] }（不分组的类别不在其中）
        self._alias_groups: dict[str, dict[str, list[str]]] = {}
        # 武器类型注册表（顶层 weapon_types）
        self._weapon_types: list[str] = []
        # 流派配置：流派名 → {main: {武器: 词条}, sub: [武器]}（顶层 schools）
        self._schools: dict[str, dict] = {}

        self._load()

    def _load(self):
        with open(self._path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        # 重置全部规则（_load 会在 UI 保存后重复调用，避免残留旧映射）
        self._base_rules.clear()
        self._affix_caps.clear()
        self._affix_units.clear()
        self._affix_pools.clear()
        self._alias_to_category.clear()
        self._alias_groups.clear()
        self._weapon_types = list(data.get("weapon_types") or [])
        self._schools = dict(data.get("schools") or {})

        # ── base_attrs ──
        # _follow: <目标部位> 声明该部位跟随目标部位的数值（单层解析）
        base_attrs = data.get("base_attrs", {})
        follows: dict[str, str] = {}
        for key in BASE_ATTR_PARTS:
            section = base_attrs.get(key, {}) or {}
            target = section.get("_follow")
            if target:
                follows[key] = target
                continue
            self._base_rules[key] = {}
            for level_str, qualities in section.items():
                if str(level_str).startswith("_"):
                    continue
                level = int(level_str)
                ranges = []
                for q in ("gold", "purple", "blue"):
                    if q not in qualities:
                        continue
                    v = qualities[q]
                    if isinstance(v, dict):
                        ranges.append(AttrRange(
                            quality=q,
                            min_val=v.get("min"),
                            max_val=v.get("max"),
                        ))
                    else:
                        val = int(v)
                        ranges.append(AttrRange(quality=q, min_val=val, max_val=val))
                self._base_rules[key][level] = LevelRule(ranges=ranges)

        # 跟随部位直接复用目标部位的规则对象
        for key, target in follows.items():
            self._base_rules[key] = self._base_rules.get(target, {})

        # ── affix_caps ──
        raw_caps = data.get("affix_caps", {})
        for category, levels in raw_caps.items():
            self._affix_caps[category] = {}
            if not isinstance(levels, dict):
                continue
            # 解析 _aliases 字段，构建映射（list=不分组 / dict=分组）
            aliases = levels.get("_aliases", [])
            if isinstance(aliases, dict):
                self._alias_groups[category] = {
                    group: list(names) for group, names in aliases.items()
                    if isinstance(names, list)
                }
                for names in self._alias_groups[category].values():
                    for alias in names:
                        self._alias_to_category[alias] = category
            elif isinstance(aliases, list):
                for alias in aliases:
                    self._alias_to_category[alias] = category
            # 解析 _pool 字段（缺省普通词条）
            pool = levels.get("_pool", POOL_NORMAL)
            self._affix_pools[category] = pool if pool == POOL_DINGYIN else POOL_NORMAL
            # 解析 _unit 字段（词组级单位，缺省空字符串）
            self._affix_units[category] = levels.get("_unit", "")
            for level_str, entry in levels.items():
                # 跳过 _aliases 等非等级 key
                if str(level_str).startswith("_"):
                    continue
                level = int(level_str)
                if isinstance(entry, dict):
                    self._affix_caps[category][level] = {
                        "cap": entry.get("cap", 0),
                    }
                else:
                    # 兼容旧格式（直接数值）
                    self._affix_caps[category][level] = {
                        "cap": entry,
                    }

    # ── 词条映射 ────────────────────────────────────────────

    def resolve_affix_category(self, affix_name: str) -> str:
        """真实词条名 → 配置类别名

        无映射则原样返回。
        映射关系从 YAML 的 _aliases 字段加载。
        """
        return self._alias_to_category.get(affix_name, affix_name)

    def get_aliases_for_category(self, category: str) -> list[str]:
        """获取某类别下的所有别名（分组类别返回全部组内词条）"""
        return [alias for alias, cat in self._alias_to_category.items() if cat == category]

    def get_alias_groups(self, category: str) -> dict[str, list[str]]:
        """获取某类别的词条分组（组名 → 词条名列表）

        未分组的类别返回空 dict。
        """
        return self._alias_groups.get(category, {})

    # ── 词库类型 ────────────────────────────────────────

    def get_affix_pool(self, affix_name: str) -> str:
        """查询词条的词库类型（自动映射到类别）

        Returns:
            POOL_NORMAL（普通词条，缺省）或 POOL_DINGYIN（定音词条）
        """
        category = self.resolve_affix_category(affix_name)
        return self._affix_pools.get(category, POOL_NORMAL)

    def is_dingyin_affix(self, affix_name: str) -> bool:
        """是否定音词条"""
        return self.get_affix_pool(affix_name) == POOL_DINGYIN

    def get_normal_affix_names(self) -> list[str]:
        """全部普通词条标准名（非定音词组的 _aliases 并集，按 YAML 声明序）

        调律规则校验与调律规则 UI 词条候选的唯一来源。
        """
        return [alias for alias, cat in self._alias_to_category.items()
                if self._affix_pools.get(cat, POOL_NORMAL) != POOL_DINGYIN]

    # ── 词条上限查询 ────────────────────────────────────────

    def get_affix_caps(self, level: int, affix_name: str) -> dict | None:
        """查询某等级某词条的上限

        Args:
            level: 装备等级
            affix_name: 真实词条名（自动映射到类别）

        Returns:
            {"cap": float, "unit": str, "chengyin": float} 或 None

        承音值：普通词条 = cap * 0.94；定音词条不受承音限制，承音值 = cap。
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
            chengyin = round(cap * _CHENGYIN_RATIO, 2)
        return {
            "cap": cap,
            "unit": self._affix_units.get(category, ""),
            "chengyin": chengyin,
        }

    def get_all_affix_categories(self) -> list[str]:
        """返回所有已配置的词条类别名"""
        return list(self._affix_caps.keys())

    # ── 武器类型 / 流派配置 ────────────────────────

    def get_weapon_types(self) -> list[str]:
        """武器类型注册表（顶层 weapon_types，reload 后实时生效）"""
        return list(self._weapon_types)

    def get_schools(self) -> dict[str, dict]:
        """流派配置（顶层 schools：流派名 → {main: {武器: 词条}, sub: [武器]}）"""
        return dict(self._schools)

    # ── 品阶推断 ────────────────────────────────────────────

    def infer_quality(self, equip_type: str | None, level: int, value: int) -> str | None:
        """根据基础属性推断品阶

        Args:
            equip_type: 装备类型（剑/枪/环/佩/冠胄/胸甲/...），可为 None
            level: 装备等级
            value: 属性值（武器范围取 max）

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
        for key, level_rules in self._base_rules.items():
            rule = level_rules.get(level)
            if rule:
                result = rule.infer_quality(value)
                if result:
                    return result
        return None


# ─── 全局单例 ─────────────────────────────────────────────

_instance: AttrRuleManager | None = None


def get_attr_rule_manager() -> AttrRuleManager:
    """获取全局 AttrRuleManager 单例"""
    global _instance
    if _instance is None:
        _instance = AttrRuleManager()
    return _instance
