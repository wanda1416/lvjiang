"""游戏配置管理器

统一管理 attributes.yaml 中的游戏基础配置数据：
1. 基础属性品阶推断（原 EquipAttrConfig）
2. 词条上限查询（含承音值）
3. 真实词条名 → 配置类别名 映射
4. 词库类型查询（普通词条 / 定音词条，YAML 中用 _pool: dingyin 声明）
5. 官方流派与武器注册表（schools 节，get_schools）

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

# 武学增效词条所在的词条类别（affix_caps 节；
# 游戏配置的武器绑定与调律规则的增伤词条候选共用）
WUXUE_CATEGORY = "指定武学增效"

# 词条归属分类（固定 6 类，与词组正交；定音词条不参与归属）
AFFIX_CATEGORY_NAMES = ("外功类", "属攻类", "三率类", "增效类", "武器类", "生存类")


# ─── 品阶推断数据结构 ──────────────────────────────────────

@dataclass
class AttrRange:
    """单个品阶的属性值规则

    区间属性（武器）：[min_val, max_val] 表示该品阶装备提供
        +min_val 最小外功攻击、+max_val 最大外功攻击（并非取值区间）。
    点值属性（首饰/防具）：min_val == max_val。
    """
    quality: str           # gold / purple / blue
    min_val: int | None = None
    max_val: int | None = None

    def matches(self, value: int | list | tuple) -> bool:
        """判定解析出的基础属性值是否精确命中本品阶。

        区间属性：解析出的区间 [c, d] 必须 c==min_val 且 d==max_val，
            而非“落在区间内”（因相邻品阶区间会重叠）。
        点值属性：解析出的标量须精确等于该值（min_val==max_val）。
        """
        if isinstance(value, (list, tuple)):
            return (len(value) >= 2
                    and value[0] == self.min_val and value[1] == self.max_val)
        # 标量：仅点值属性（min==max）可命中
        return self.min_val == self.max_val == value


@dataclass
class LevelRule:
    """某个分类在某个等级的品阶规则"""
    ranges: list[AttrRange] = field(default_factory=list)

    def infer_quality(self, value: int | list | tuple) -> str | None:
        for r in self.ranges:
            if r.matches(value):
                return r.quality
        return None


# ─── equip_type → 配置 key 映射 ─────────────────────────────

_TYPE_TO_KEY = {
    # 武器类型 → weapon
    "陌刀": "weapon", "舞绫鼓": "weapon", "双刀": "weapon",
    "绳镖": "weapon", "横刀": "weapon", "手甲": "weapon",
    "剑": "weapon", "枪": "weapon", "扇": "weapon", "伞": "weapon",
    # 首饰
    "环": "ring",
    "佩": "pendant",
    # 防具
    "冠胄": "head", "胫甲": "leg", "腕甲": "wrist",
    "胸甲": "chest",
}

# 配置 key → equip_type 反向映射（仅一一对应的首饰/防具；
# 武器 key 对应多种武器类型，无法反推具体 type）
_KEY_TO_TYPE = {
    "ring": "环", "pendant": "佩",
    "head": "冠胄", "chest": "胸甲", "leg": "胫甲", "wrist": "腕甲",
}

# 七个装备部位（base_attrs 的全部 key，与 UI 展示顺序一致）
BASE_ATTR_PARTS = (
    "weapon", "ring", "pendant",
    "head", "chest", "leg", "wrist",
)

# 七个装备部位的标准中文名（词条部位候选，与 BASE_ATTR_PARTS 同序，
# 与 tuning_rules.models.QUALITY_PARTS 对齐）
EQUIP_PART_NAMES = ("武器", "环", "佩", "冠胄", "胸甲", "胫甲", "腕甲")


# ─── 属性规则管理器 ─────────────────────────────────────────

class GameConfigManager:
    """属性规则管理器

    从 attributes.yaml 加载全部规则，提供：
    - 品阶推断（base_attrs）
    - 词条上限查询（affix_caps）
    - 词条名映射（真实词条 → 配置类别）
    """

    def __init__(self, path: str | Path | None = None):
        if path is None:
            from lvjiang.constants import PROJECT_ROOT
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
        # 词条归属：归属名 → [词条名]（顶层 affix_categories，固定 5 类）
        self._affix_categories: dict[str, list[str]] = {}
        # 词条归属反查：词条名 → 归属名
        self._affix_to_category: dict[str, str] = {}
        # 词条部位：词条名 → [部位中文名]（顶层 affix_parts，缺省全部位）
        self._affix_parts: dict[str, list[str]] = {}
        # 武器类型注册表（顶层 weapon_types）
        self._weapon_types: list[str] = []
        # 武器 → 武学增效词条映射（weapon_types 中每项的 wuxue_affix 字段）
        self._weapon_wuxue_affixes: dict[str, str] = {}
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
        self._affix_categories.clear()
        self._affix_to_category.clear()
        self._affix_parts.clear()
        self._weapon_types.clear()
        self._weapon_wuxue_affixes.clear()
        self._schools.clear()

        # ── weapon_types（支持 dict 列表格式：[{name, wuxue_affix}, ...]）──
        raw_weapon_types = data.get("weapon_types") or []
        for entry in raw_weapon_types:
            if isinstance(entry, dict):
                name = str(entry.get("name", ""))
                if name:
                    self._weapon_types.append(name)
                    affix = entry.get("wuxue_affix")
                    if affix:
                        self._weapon_wuxue_affixes[name] = str(affix)
            else:
                # 兼容旧格式（纯字符串）
                name = str(entry)
                if name:
                    self._weapon_types.append(name)
        self._schools = dict(data.get("schools") or {})

        # ── affix_categories（顶层；固定 5 类归属→词条名列表）──
        raw_categories = data.get("affix_categories") or {}
        for cat in AFFIX_CATEGORY_NAMES:
            raw_names = raw_categories.get(cat)
            names = [str(n) for n in raw_names] if isinstance(raw_names, list) else []
            self._affix_categories[cat] = names
            for name in names:
                self._affix_to_category[name] = cat

        # ── affix_parts（顶层；词条名→可出现部位列表，未配置=全部位）──
        raw_parts = data.get("affix_parts") or {}
        for name, parts in raw_parts.items():
            if not isinstance(parts, list):
                continue
            valid = [p for p in parts if p in EQUIP_PART_NAMES]
            if valid:
                self._affix_parts[str(name)] = valid

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

    # ── 词条归属（固定 5 类，与词组正交） ───────────────

    def get_affix_categories(self) -> dict[str, list[str]]:
        """词条归属映射（按 AFFIX_CATEGORY_NAMES 顺序：归属名 → 词条名列表副本）"""
        return {cat: list(self._affix_categories.get(cat, []))
                for cat in AFFIX_CATEGORY_NAMES}

    def get_affix_category(self, affix_name: str) -> str:
        """词条名 → 归属名（无归属返回空串）"""
        return self._affix_to_category.get(affix_name, "")

    def get_affix_parts(self, affix_name: str) -> list[str]:
        """词条可出现的装备部位（未配置 = 全部七个部位）"""
        return list(self._affix_parts.get(affix_name) or EQUIP_PART_NAMES)

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

    def get_weapon_wuxue_affix(self, weapon: str) -> str:
        """武器对应的武学增效词条（来自 weapon_types 的 wuxue_affix 字段）

        未配置时返回空字符串。
        """
        return self._weapon_wuxue_affixes.get(weapon, "")

    def get_all_weapon_wuxue_affixes(self) -> dict[str, str]:
        """全部武器 → 武学增效词条映射"""
        return dict(self._weapon_wuxue_affixes)

    def get_wuxue_affix_names(self) -> list[str]:
        """全部指定武学增效词条（affix_caps 该类别的 _aliases）

        调律规则 UI 增伤词条候选的唯一来源。
        """
        return self.get_aliases_for_category(WUXUE_CATEGORY)

    def get_schools(self) -> dict[str, dict]:
        """流派配置（顶层 schools：流派名 → {main: {武器: 词条}, sub: [武器]}）"""
        return dict(self._schools)

    # ── 品阶推断 ────────────────────────────────────────────

    def infer_quality(self, equip_type: str | None, level: int,
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
        for key, level_rules in self._base_rules.items():
            rule = level_rules.get(level)
            if rule:
                result = rule.infer_quality(value)
                if result:
                    return result
        return None

    def infer_level_quality(
        self, equip_type: str | None, value: int | list | tuple
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

    def infer_type_by_value(self, value: int | list | tuple) -> str | None:
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


# ─── 全局单例 ─────────────────────────────────────────────

_instance: GameConfigManager | None = None


def get_game_config() -> GameConfigManager:
    """获取全局 GameConfigManager 单例"""
    global _instance
    if _instance is None:
        _instance = GameConfigManager()
    return _instance
