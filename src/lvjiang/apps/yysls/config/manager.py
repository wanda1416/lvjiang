"""游戏配置管理器

从 game_config.yaml 加载全部规则，提供品阶推断、词条上限查询、
词条名映射、流派注册表等功能。

数据来源：config/system/yysls/game_config.yaml
"""

from __future__ import annotations

import copy
from datetime import date
from pathlib import Path

import yaml

from ....i18n import tr
from .constants import (
    _CHENGYIN_RATIO,
    _KEY_TO_TYPE,
    _TYPE_TO_KEY,
    AFFIX_CATEGORY_NAMES,
    BASE_ATTR_PARTS,
    EQUIP_PART_NAMES,
    POOL_DINGYIN,
    POOL_NORMAL,
    WUXUE_CATEGORY,
)
from .models import AttrRange, LevelConfig, LevelRule, SeasonConfig


def _parse_date(value) -> date | None:
    """解析日期值：支持 date 对象、ISO 格式字符串（YYYY-MM-DD），无效返回 None"""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


class GameConfigManager:
    """属性规则管理器

    从 game_config.yaml 加载全部规则，提供：
    - 品阶推断（base_attrs）
    - 词条上限查询（affix_caps）
    - 词条名映射（真实词条 → 配置类别）
    """

    def __init__(self, path: str | Path | None = None):
        # path 非空（测试/孤立文件）时直读；否则经 resolver 读合并视图
        self._path = Path(path) if path is not None else None
        self._raw: dict = {}

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
        # 外部简称：简称 → [精准词条名]（顶层 affix_aliases）
        self._external_alias_to_affixes: dict[str, list[str]] = {}
        # 精准词条名 → [外部简称]
        self._affix_external_aliases: dict[str, list[str]] = {}
        # 武器类型注册表（顶层 weapon_types）
        self._weapon_types: list[str] = []
        # 武器 → 武学增效词条映射（weapon_types 中每项的 wuxue_affix 字段）
        self._weapon_wuxue_affixes: dict[str, str] = {}
        # 流派配置：流派名 → {main: {武器: 词条}, sub: [武器]}（顶层 schools）
        self._schools: dict[str, dict] = {}
        # 等级配置：等级 → LevelConfig（顶层 level_configs）
        self._level_configs: list[LevelConfig] = []
        # 赛季配置：赛季编号 → SeasonConfig（顶层 season_configs）
        self._season_configs: list[SeasonConfig] = []

        self._load()

    def _load(self):
        if self._path is not None:
            with open(self._path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        else:
            from lvjiang.core.config.resolver import get_resolver
            data = get_resolver().load_merged("yysls/game_config.yaml")
        self._raw = data

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
        self._external_alias_to_affixes.clear()
        self._affix_external_aliases.clear()
        self._weapon_types.clear()
        self._weapon_wuxue_affixes.clear()
        self._schools.clear()
        self._level_configs.clear()
        self._season_configs.clear()

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

        # ── affix_aliases（精准词条名→外部表格等系统使用的多个简称）──
        raw_external_aliases = data.get("affix_aliases") or {}
        for affix_name, aliases in raw_external_aliases.items():
            if not isinstance(aliases, list):
                continue
            exact_name = str(affix_name)
            cleaned = list(dict.fromkeys(
                str(alias).strip() for alias in aliases if str(alias).strip()
            ))
            if not cleaned:
                continue
            self._affix_external_aliases[exact_name] = cleaned
            for alias in cleaned:
                self._external_alias_to_affixes.setdefault(alias, []).append(exact_name)

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

        # ── level_configs（顶层等级配置）──
        raw_levels = data.get("level_configs") or []
        seen_levels: set[int] = set()
        for item in raw_levels:
            if not isinstance(item, dict):
                continue
            level = item.get("level")
            if not isinstance(level, int) or level in seen_levels:
                continue
            seen_levels.add(level)
            self._level_configs.append(LevelConfig(
                level=level,
                allow_reset=item.get("allow_reset"),
                min_material_count=item.get("min_material_count"),
                judge_resistance=item.get("judge_resistance"),
                buff_resistance=item.get("buff_resistance"),
            ))
        # 按等级排序
        self._level_configs.sort(key=lambda c: c.level)

        # ── season_configs（顶层赛季配置）──
        raw_seasons = data.get("season_configs") or []
        seen_seasons: set[int] = set()
        for item in raw_seasons:
            if not isinstance(item, dict):
                continue
            season_number = item.get("season_number")
            if not isinstance(season_number, int) or season_number in seen_seasons:
                continue
            seen_seasons.add(season_number)
            self._season_configs.append(SeasonConfig(
                season_number=season_number,
                name=item.get("name", ""),
                start_date=_parse_date(item.get("start_date")),
                end_date=_parse_date(item.get("end_date")),
                first_half_end_date=_parse_date(item.get("first_half_end_date")),
                equip_level=item.get("equip_level"),
            ))
        # 按赛季编号排序
        self._season_configs.sort(key=lambda c: c.season_number)

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

    def get_affix_aliases(self, affix_name: str) -> list[str]:
        """返回精准词条配置的外部简称。"""
        return list(self._affix_external_aliases.get(affix_name) or [])

    def get_affix_names_for_alias(self, alias: str) -> list[str]:
        """严格按外部简称返回精准词条名，不执行任何模糊匹配。"""
        return list(self._external_alias_to_affixes.get(alias) or [])

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

    # ── 装备基础属性查询 ────────────────────────────────────────

    def get_base_attr_values(
        self, part: str, level: int, quality: str
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
        """全部指定武学增效词条（affix_caps 这类别的 _aliases）

        调律规则 UI 增伤词条候选的唯一来源。
        """
        return self.get_aliases_for_category(WUXUE_CATEGORY)

    def get_schools(self) -> dict[str, dict]:
        """流派配置（顶层 schools：流派名 → {main: {武器: 词条}, sub: [武器]}）"""
        return dict(self._schools)

    def get_school_attr(self, school: str) -> str | None:
        """获取流派的属性类型（鸣金/裂石/破竹/牵丝）"""
        cfg = self._schools.get(school, {})
        return cfg.get("attr") if cfg else None

    def get_graduation_schemes(self, school: str) -> list[str]:
        """获取流派已注册的毕业率方案，保持配置声明顺序。"""
        cfg = self._schools.get(school, {})
        schemes = cfg.get("schemes") if cfg else None
        if not isinstance(schemes, list):
            return []
        return list(dict.fromkeys(
            str(name).strip() for name in schemes if str(name).strip()
        ))

    # ── 等级配置 ────────────────────────────────────────────

    def get_level_configs(self) -> list[LevelConfig]:
        """等级配置列表（按等级排序的副本）"""
        return list(self._level_configs)

    def level_config_for(self, level: int) -> LevelConfig | None:
        """按等级查找配置条目（精确匹配），未找到返回 None"""
        for cfg in self._level_configs:
            if cfg.level == level:
                return cfg
        return None

    # ── 赛季配置 ────────────────────────────────────────────

    def get_season_configs(self) -> list[SeasonConfig]:
        """赛季配置列表（按赛季编号排序的副本）"""
        return list(self._season_configs)

    def season_config_for(self, season_number: int) -> SeasonConfig | None:
        """按赛季编号查找配置条目（精确匹配），未找到返回 None"""
        for cfg in self._season_configs:
            if cfg.season_number == season_number:
                return cfg
        return None

    def current_season(self) -> SeasonConfig | None:
        """获取当前赛季（根据当前日期在 start_date 和 end_date 之间判断）

        无匹配返回 None。
        """
        today = date.today()
        for cfg in self._season_configs:
            if cfg.start_date and cfg.end_date:
                if cfg.start_date <= today <= cfg.end_date:
                    return cfg
        return None

    # ── 原始数据访问与保存（UI 编辑用） ────────────────────────

    def get_raw(self) -> dict:
        """返回原始 YAML 数据的深拷贝（UI 编辑用）"""
        return copy.deepcopy(self._raw)

    def save(self, data: dict) -> None:
        """校验并写盘，然后 reload"""
        # 简单校验：确保是 dict 且有 level_configs
        if not isinstance(data, dict):
            raise ValueError(tr("数据必须是 dict"))
        # 写盘
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, sort_keys=False)
        else:
            from lvjiang.core.config.resolver import get_resolver
            get_resolver().save_merged("yysls/game_config.yaml", data)
        # 重新加载
        self._load()

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
        for _key, level_rules in self._base_rules.items():
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
