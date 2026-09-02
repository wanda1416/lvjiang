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


def _string_list(value) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(
        str(item).strip() for item in value if str(item).strip()))


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
        # 分类级词条部位：category → [部位中文名]（affix_caps 内 _parts 字段）
        self._category_parts: dict[str, list[str]] = {}
        # 外部简称：简称 → [精准词条名]（顶层 affix_aliases）
        self._external_alias_to_affixes: dict[str, list[str]] = {}
        # 精准词条名 → [外部简称]
        self._affix_external_aliases: dict[str, list[str]] = {}
        # 武器类型注册表（顶层 weapon_types）
        self._weapon_types: list[str] = []
        # 武器 → 武学增效词条映射（weapon_types 中每项的 wuxue_affix 字段）
        self._weapon_wuxue_affixes: dict[str, str] = {}
        self._weapon_name_suffixes: dict[str, list[str]] = {}
        self._weapon_type_aliases: dict[str, list[str]] = {}
        self._part_name_suffixes: dict[str, list[str]] = {}
        self._part_type_aliases: dict[str, list[str]] = {}
        self._equipment_name_series: dict[str, dict[int, str]] = {}
        # 武学注册表：武学名 → {"weapon": 武器, "attr": 属性}（顶层 martial_arts）
        # 武器和属性都是武学的固有属性，流派/玩法只引用武学，不再各自录入武器——
        # 那会让 weapon 和 martial_art 两个字段可以互相矛盾且无人校验。
        self._martial_arts: dict[str, dict] = {}
        # 玩法注册表：玩法名 → 定义（顶层 playstyles）。
        # 玩法决定「调律方向」——要什么增伤、定什么音；流派只决定毕业率计算。
        # 混搭因此是「有玩法、无流派」：能调律，算不了毕业率，这是自然降级
        # 而不是异常。
        self._playstyles: dict[str, dict] = {}
        # 流派配置：流派名 → {main: {武器: 词条}, sub: [武器]}（顶层 schools）
        self._schools: dict[str, dict] = {}
        # 等级配置：等级 → LevelConfig（顶层 level_configs）
        self._level_configs: list[LevelConfig] = []
        # 赛季配置：赛季编号 → SeasonConfig（顶层 season_configs）
        self._season_configs: list[SeasonConfig] = []
        # 基础属性名（从 _attr 字段收集：外功攻击/气血最大值/...）
        self._base_attr_names: list[str] = []
        # 首词条候选（从 base_attrs 各部位的 _first_affixes 字段收集）
        self._first_affixes: dict[str, list[str]] = {}

        self._load()

    def reload(self) -> None:
        """重新读取配置；resolver 会先检查文件印记再决定是否解析。"""
        self._load()

    def _load(self):
        self._type_to_group_cache = None  # 重置缓存
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
        self._category_parts.clear()
        self._external_alias_to_affixes.clear()
        self._base_attr_names.clear()
        self._first_affixes.clear()
        self._affix_external_aliases.clear()
        self._martial_arts.clear()
        self._playstyles.clear()
        self._weapon_types.clear()
        self._weapon_wuxue_affixes.clear()
        self._weapon_name_suffixes.clear()
        self._weapon_type_aliases.clear()
        self._part_name_suffixes.clear()
        self._part_type_aliases.clear()
        self._equipment_name_series.clear()
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
                    self._weapon_name_suffixes[name] = _string_list(
                        entry.get("name_suffixes"))
                    aliases = _string_list(entry.get("type_aliases"))
                    self._weapon_type_aliases[name] = aliases or [name]
            else:
                # 兼容旧格式（纯字符串）
                name = str(entry)
                if name:
                    self._weapon_types.append(name)
                    self._weapon_name_suffixes[name] = []
                    self._weapon_type_aliases[name] = [name]
        for entry in (data.get("martial_arts") or []):
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            if not name:
                continue
            self._martial_arts[name] = {
                "weapon": str(entry.get("weapon") or "").strip(),
                "attr": str(entry.get("attr") or "").strip(),
            }
        for entry in (data.get("playstyles") or []):
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            if not name:
                continue
            self._playstyles[name] = {
                "school": str(entry.get("school") or "").strip(),
                "attr": str(entry.get("attr") or "").strip(),
                "arts": [str(a).strip() for a in (entry.get("arts") or [])
                         if str(a).strip()],
                "main_weapon": str(entry.get("main_weapon") or "").strip(),
                "sub_weapon": str(entry.get("sub_weapon") or "").strip(),
                "main_damage": str(entry.get("main_damage") or "").strip(),
                "sub_damage": str(entry.get("sub_damage") or "").strip(),
                "output_dingyin": str(entry.get("output_dingyin") or "").strip(),
                "defense_dingyin": str(
                    entry.get("defense_dingyin") or "").strip(),
                # 玩法说明元数据：只供配置/展示，不参与评级、
                # 自动调律或毕业率计算。缺省值只用于兼容旧配置。
                "all_skill_requirement": str(entry.get(
                    "all_skill_requirement") or "需要").strip(),
                "qishu_requirement": str(entry.get(
                    "qishu_requirement") or "不需要").strip(),
                "unit_requirement": str(entry.get(
                    "unit_requirement") or "不需要").strip(),
            }
        self._schools = dict(data.get("schools") or {})
        for group, levels in (data.get("equipment_name_series") or {}).items():
            if isinstance(levels, dict):
                self._equipment_name_series[str(group)] = {
                    int(level): str(name).strip()
                    for level, name in levels.items() if str(name).strip()
                }

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
        # 注意：_follow 仅影响基础属性数值，首词条(_first_affixes)独立配置，不跟随
        base_attrs = data.get("base_attrs", {})
        follows: dict[str, str] = {}
        for key in BASE_ATTR_PARTS:
            section = base_attrs.get(key, {}) or {}
            # 首词条独立解析（不受 _follow 影响）
            first_affixes = section.get("_first_affixes")
            if isinstance(first_affixes, list) and first_affixes:
                self._first_affixes[key] = [str(a) for a in first_affixes]
            part_type = _KEY_TO_TYPE.get(key)
            if part_type:
                self._part_name_suffixes[part_type] = _string_list(
                    section.get("_name_suffixes"))
                aliases = _string_list(section.get("_type_aliases"))
                self._part_type_aliases[part_type] = aliases or [part_type]
            # _follow 仅影响基础属性数值
            target = section.get("_follow")
            if target:
                follows[key] = target
                continue
            # 收集基础属性名（_attr 字段）
            attr_name = section.get("_attr")
            if attr_name and attr_name not in self._base_attr_names:
                self._base_attr_names.append(attr_name)
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

        # 跟随部位直接复用目标部位的基础属性规则（首词条独立，不跟随）
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
            # 解析 _parts 字段（分类级词条部位，缺省全部位）
            raw_parts = levels.get("_parts")
            if isinstance(raw_parts, list) and raw_parts:
                self._category_parts[category] = [
                    p for p in raw_parts if p in EQUIP_PART_NAMES
                ]
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
                # 兼容尚未写入新能力字段的旧用户配置；显式 false 仍优先。
                allow_chengyin=bool(item.get("allow_chengyin", level >= 91)),
                allow_retransfer=bool(
                    item.get("allow_retransfer", level >= 105)),
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

    @property
    def base_attr_names(self) -> list[str]:
        """所有合法基础属性名（从 game_config.yaml 的 _attr 字段收集）

        如 ['外功攻击', '最小外功攻击', '最大外功攻击', '气血最大值']
        """
        return list(self._base_attr_names)

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

    def get_affix_aliases(self, affix_name: str) -> list[str]:
        """返回精准词条配置的外部简称。"""
        return list(self._affix_external_aliases.get(affix_name) or [])

    def get_alias_groups_for_category(self, category: str) -> dict[str, list[str]]:
        """返回词条类别的标准名称分组快照。"""
        return copy.deepcopy(self._alias_groups.get(category) or {})

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

    def get_dingyin_affix_names(self) -> list[str]:
        """全部定音词条标准名（定音词组的 _aliases 并集，按 YAML 声明序）"""
        return [alias for alias, cat in self._alias_to_category.items()
                if self._affix_pools.get(cat, POOL_NORMAL) == POOL_DINGYIN]

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

    def get_type_to_group(self) -> dict[str, str]:
        """装备 type → 分组 key 的全局映射。

        包含所有武器具体名称（剑/枪/扇…）→ "weapon"，
        以及非武器部位名（环/佩/冠胄…）→ 对应 group_key。
        武器名称从配置动态加载，新增武器自动生效。
        """
        if not hasattr(self, '_type_to_group_cache') or self._type_to_group_cache is None:
            from lvjiang.i18n import tr
            mapping: dict[str, str] = {}
            # 所有武器具体名称 → "weapon"
            for wt in self._weapon_types:
                mapping[wt] = "weapon"
            # 武器显示名（对话框部位下拉用）
            mapping[tr("武器")] = "weapon"
            # 非武器部位
            mapping.update({
                tr("环"): "ring",
                tr("佩"): "pendant",
                tr("冠胄"): "head",
                tr("胸甲"): "chest",
                tr("胫甲"): "leg",
                tr("腕甲"): "wrist",
            })
            self._type_to_group_cache = mapping
        return dict(self._type_to_group_cache)

    def get_group_to_part(self) -> dict[str, str]:
        """分组 key → 部位显示名的反向映射。"""
        from lvjiang.i18n import tr
        return {
            "weapon": tr("武器"),
            "ring": tr("环"),
            "pendant": tr("佩"),
            "head": tr("冠胄"),
            "chest": tr("胸甲"),
            "leg": tr("胫甲"),
            "wrist": tr("腕甲"),
        }

    def get_first_affixes(self, part: str) -> list[str]:
        """指定部位的首词条候选列表（来自 base_attrs.<part>._first_affixes）

        part 为 group_key（weapon/ring/pendant/head/chest/leg/wrist）。
        未配置时返回空列表。
        """
        return list(self._first_affixes.get(part, []))

    def get_weapon_wuxue_affix(self, weapon: str) -> str:
        """武器对应的武学增效词条（来自 weapon_types 的 wuxue_affix 字段）

        未配置时返回空字符串。
        """
        return self._weapon_wuxue_affixes.get(weapon, "")

    def get_all_weapon_wuxue_affixes(self) -> dict[str, str]:
        """全部武器 → 武学增效词条映射"""
        return dict(self._weapon_wuxue_affixes)

    def infer_equipment_type_from_name(self, equipment_name: str) -> str | None:
        """按配置的标准名称反查具体武器或非武器部位。"""
        matches: list[tuple[int, str]] = []
        mappings = [
            *self._weapon_name_suffixes.items(),
            *self._part_name_suffixes.items(),
        ]
        for equip_type, suffixes in mappings:
            for suffix in suffixes:
                if equipment_name.endswith(suffix):
                    matches.append((len(suffix), equip_type))
        if not matches:
            return None
        longest = max(length for length, _equip_type in matches)
        types = {equip_type for length, equip_type in matches if length == longest}
        return types.pop() if len(types) == 1 else None

    def infer_equipment_type_from_label(self, type_label: str) -> str | None:
        """按配置的部位名称反查规范类型，最长名称优先。"""
        text = str(type_label or "").strip()
        matches: list[tuple[int, str]] = []
        mappings = [
            *self._weapon_type_aliases.items(),
            *self._part_type_aliases.items(),
        ]
        for equip_type, aliases in mappings:
            for alias in aliases:
                if alias and alias in text:
                    matches.append((len(alias), equip_type))
        if not matches:
            return None
        longest = max(length for length, _equip_type in matches)
        types = {equip_type for length, equip_type in matches if length == longest}
        return types.pop() if len(types) == 1 else None

    def get_equipment_name_series(self, group: str) -> dict[int, str]:
        """等阶名称配置；仅用于名称合法性佐证，不参与等级推断。"""
        return dict(self._equipment_name_series.get(group, {}))

    def get_wuxue_affix_names(self) -> list[str]:
        """全部指定武学增效词条（affix_caps 这类别的 _aliases）

        调律规则 UI 增伤词条候选的唯一来源。
        """
        return self.get_aliases_for_category(WUXUE_CATEGORY)

    def get_martial_arts(self) -> dict[str, dict]:
        """武学注册表：武学名 → {"weapon", "attr"}。"""
        return {k: dict(v) for k, v in self._martial_arts.items()}

    def get_martial_art(self, name: str) -> dict | None:
        entry = self._martial_arts.get(str(name or "").strip())
        return dict(entry) if entry else None

    def get_martial_art_weapon(self, name: str) -> str:
        """武学对应的武器；未登记返回空串。

        流派/玩法里的武器一律由此派生，不再单独录入。
        """
        return (self._martial_arts.get(str(name or "").strip()) or {}).get(
            "weapon", "")

    def get_martial_art_attr(self, name: str) -> str:
        """武学对应的属性；未登记返回空串。"""
        return (self._martial_arts.get(str(name or "").strip()) or {}).get(
            "attr", "")

    def get_martial_arts_by_weapon(self, weapon: str) -> list[str]:
        target = str(weapon or "").strip()
        return sorted(k for k, v in self._martial_arts.items()
                      if v.get("weapon") == target)

    def check_school_weapon_consistency(self) -> list[str]:
        """流派里录入的 weapon 与武学派生的武器是否一致。

        拆分武学之前这两个字段各录各的，写成「武器=枪 + 武学=无名剑法」也存得
        下来，然后毕业率按枪算、词条按剑法找，全程静默。这里把它变成能查出来的。
        """
        problems: list[str] = []
        for school, cfg in self._schools.items():
            for side in ("main", "sub"):
                entry = (cfg or {}).get(side) or {}
                art = str(entry.get("martial_art") or "").strip()
                if not art:
                    continue
                if art not in self._martial_arts:
                    problems.append(f"流派 {school}.{side} 的武学 {art} 未登记")
                    continue
                derived = self._martial_arts[art]["weapon"]
                recorded = str(entry.get("weapon") or "").strip()
                if recorded and recorded != derived:
                    problems.append(
                        f"流派 {school}.{side} 录入武器 {recorded}，"
                        f"但武学 {art} 属于 {derived}")
        return problems

    def get_affix_names_in_category(self, category: str) -> list[str]:
        """某词条类别下的全部具体词条名（含分组的取并集）。"""
        names: list[str] = []
        for group in (self._alias_groups.get(category) or {}).values():
            names.extend(group)
        if not names:
            names = [a for a, c in self._alias_to_category.items()
                     if c == category]
        return sorted(dict.fromkeys(names))

    def get_affix_names_in_group(self, category: str, group: str) -> list[str]:
        """返回词组类别中指定分组的精准词条名。

        ``指定技能增效`` 以流派名分组。这里直接读取结构化分组，不能再靠
        武学名前缀猜测：醉拳等词条会使用技能体系名，未必以武学名开头。
        """
        names = (self._alias_groups.get(str(category or ""), {})
                 .get(str(group or ""), []))
        return list(dict.fromkeys(names))

    def get_affix_category_parts(self, category: str) -> list[str]:
        """词条类别声明的适用部位（affix_caps 内的 _parts）。

        输出/防御的划分直接用它，不另建分组：`指定技能增效` 的 _parts 就是
        防具四件，粒度比全局二分更准，而且加部位时只改配置。
        """
        return list(self._category_parts.get(str(category or ""), []))

    def get_playstyles(self) -> dict[str, dict]:
        """玩法注册表：玩法名 → 定义。"""
        return {k: dict(v) for k, v in self._playstyles.items()}

    def get_playstyle(self, name: str) -> dict | None:
        entry = self._playstyles.get(str(name or "").strip())
        return dict(entry) if entry else None

    def get_playstyles_for_arts(self, arts) -> list[str]:
        """哪些玩法登记了这组武学。

        主副只是顺序标签，判别式是「要谁的增伤」而不是谁在前，因此按**无序**
        集合匹配：选了 (斩雪刀法, 十方破阵) 时纯唐和双切都应列出来，由用户挑。
        """
        target = {str(a).strip() for a in (arts or []) if str(a).strip()}
        if not target:
            return []
        return sorted(name for name, cfg in self._playstyles.items()
                      if set(cfg.get("arts") or []) == target)

    def get_playstyle_dingyin(self, name: str, part: str) -> str:
        """该玩法在某部位应有的定音；未配置返回空串。

        输出/防御的划分沿用词条类别自己的 _parts 声明，不另建分组。
        """
        cfg = self._playstyles.get(str(name or "").strip())
        if not cfg:
            return ""
        defense_parts = self.get_affix_category_parts("指定技能增效")
        key = "defense_dingyin" if part in defense_parts else "output_dingyin"
        return cfg.get(key, "")

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


def reset_game_config() -> None:
    """重置全局 GameConfigManager 单例（测试隔离用）"""
    global _instance
    _instance = None
