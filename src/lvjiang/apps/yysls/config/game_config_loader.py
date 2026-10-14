"""领域配置解析阶段；只填充统一的 GameConfigManager 实例。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from .affix_levels import LevelRange, parse_entry, parse_range
from .constants import (
    _KEY_TO_TYPE,
    AFFIX_CATEGORY_NAMES,
    BASE_ATTR_PARTS,
    EQUIP_PART_NAMES,
    POOL_DINGYIN,
    POOL_NORMAL,
    STACK_MAX,
    STACK_SUM,
    normalize_equip_part,
)
from .game_config_parsers import (
    _parse_affix_upgrades,
    _parse_date,
    _parse_tuning_stones,
    _string_list,
    derive_chengyin_cap,
)
from .models import (
    AttrRange,
    LevelConfig,
    LevelRule,
    SeasonConfig,
)

if TYPE_CHECKING:
    from .manager import GameConfigManager

def parse_basic(self: GameConfigManager, data: dict) -> None:
    raw_basic = data.get("basic_config") or {}
    cooldown_days = raw_basic.get("equipment_cooldown_days", 5)
    self._equipment_cooldown_days = (
        cooldown_days
        if isinstance(cooldown_days, int) and not isinstance(cooldown_days, bool)
        and 1 <= cooldown_days <= 365
        else 5
    )
    cooldown_carryover = raw_basic.get(
        "equipment_cooldown_carryover", True)
    self._equipment_cooldown_carryover = (
        cooldown_carryover
        if isinstance(cooldown_carryover, bool)
        else True
    )



def parse_weapons(self: GameConfigManager, data: dict) -> None:
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
            logger.error("weapon_types 条目必须是对象，已跳过；请在游戏设置重新保存")


def parse_arts(self: GameConfigManager, data: dict) -> None:
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
            # 玩法神力要求：评级仍由规则决定；智能调律据此固定填入
            # 当前部位的必需词条。缺省值用于兼容旧配置。
            "all_skill_requirement": str(entry.get(
                "all_skill_requirement") or "需要").strip(),
            "qishu_requirement": str(entry.get(
                "qishu_requirement") or "不需要").strip(),
            "unit_requirement": str(entry.get(
                "unit_requirement") or "不需要").strip(),
            # 扫描全部备战方案时的匹配关键字。同一流派同一武学组合下可能
            # 并存多个玩法（火拳/纯奶），方案名未必写着玩法名，靠它兜底。
            "match_keywords": _string_list(entry.get("match_keywords")),
        }
    self._schools = dict(data.get("schools") or {})


def parse_sets(self: GameConfigManager, data: dict) -> None:
    for group, levels in (data.get("equipment_name_series") or {}).items():
        if isinstance(levels, dict):
            self._equipment_name_series[str(group)] = {
                int(level): str(name).strip()
                for level, name in levels.items() if str(name).strip()
            }
    raw_sets = data.get("equipment_sets") or {}
    for kind, levels in (data.get("gongjue_bonuses") or {}).items():
        if not isinstance(levels, dict):
            continue
        ranges: dict[tuple[int, int], float] = {}
        for key, value in levels.items():
            bounds = str(key).split("-", 1)
            try:
                low = int(bounds[0])
                high = int(bounds[-1])
                bonus = float(value)
            except (ValueError, TypeError):
                continue
            if 0 < low <= high and bonus >= 0:
                ranges[low, high] = bonus
        self._gongjue_bonuses[str(kind)] = ranges
    if isinstance(raw_sets, dict):
        for side in ("left", "right"):
            entries = raw_sets.get(side) or {}
            if not isinstance(entries, dict):
                continue
            for key, entry in entries.items():
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name") or "").strip()
                if not name:
                    continue
                normalized = {"name": name}
                if side == "left":
                    normalized.update({
                        "two_piece_affix": str(
                            entry.get("two_piece_affix") or "").strip(),
                        "recommended_right": str(
                            entry.get("recommended_right") or "").strip(),
                        "recommended_school": str(
                            entry.get("recommended_school") or "").strip(),
                    })
                self._equipment_sets[side][str(key)] = normalized



def parse_affix_catalogue(self: GameConfigManager, data: dict) -> None:
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
        valid = [
            normalized
            for p in parts
            if (normalized := normalize_equip_part(p)) in EQUIP_PART_NAMES
        ]
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



def parse_base_attrs(self: GameConfigManager, data: dict) -> None:
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
            names: list[str] = []
            ranges: dict[str, LevelRange] = {}
            for raw_entry in first_affixes:
                name, level_range = parse_entry(raw_entry)
                if not name:
                    continue
                names.append(name)
                if level_range.configured:
                    ranges[name] = level_range
            self._first_affixes[key] = names
            if ranges:
                self._first_affix_levels[key] = ranges
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
            quality_ranges: list[AttrRange] = []
            for q in ("gold", "purple", "blue"):
                if q not in qualities:
                    continue
                v = qualities[q]
                if isinstance(v, dict):
                    quality_ranges.append(AttrRange(
                        quality=q,
                        min_val=v.get("min"),
                        max_val=v.get("max"),
                    ))
                else:
                    val = int(v)
                    quality_ranges.append(AttrRange(quality=q, min_val=val, max_val=val))
            self._base_rules[key][level] = LevelRule(ranges=quality_ranges)

    # 跟随部位直接复用目标部位的基础属性规则（首词条独立，不跟随）
    for key, target in follows.items():
        self._base_rules[key] = self._base_rules.get(target, {})



def parse_affix_caps(self: GameConfigManager, data: dict) -> None:
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
        # 解析 _upgrades 字段（词组内的跨等级升级规则）。规则跟着词组走：
        # 词条升级基本都是同一组内部的合并，写在组里才看得见上下文。
        for raw_rule in levels.get("_upgrades") or []:
            self._affix_upgrades.extend(_parse_affix_upgrades(raw_rule))
        # 解析 _alias_levels 字段（别名 → 生效等级范围）
        raw_alias_levels = levels.get("_alias_levels")
        if isinstance(raw_alias_levels, dict):
            for alias, raw_range in raw_alias_levels.items():
                level_range = parse_range(raw_range)
                if level_range.configured:
                    self._affix_levels[str(alias)] = level_range
        # 解析 _pool 字段（缺省普通词条）
        pool = levels.get("_pool", POOL_NORMAL)
        self._affix_pools[category] = pool if pool == POOL_DINGYIN else POOL_NORMAL
        # 解析 _unit 字段（词组级单位，缺省空字符串）
        self._affix_units[category] = levels.get("_unit", "")
        # 解析 _stack 字段（同名叠加方式，缺省累加）
        stack = levels.get("_stack", STACK_SUM)
        self._affix_stacks[category] = (
            STACK_MAX if stack == STACK_MAX else STACK_SUM)
        # 解析 _parts 字段（分类级词条部位，缺省全部位）
        raw_parts = levels.get("_parts")
        if isinstance(raw_parts, list) and raw_parts:
            self._category_parts[category] = [
                normalized
                for p in raw_parts
                if (normalized := normalize_equip_part(p))
                in EQUIP_PART_NAMES
            ]
        for level_str, entry in levels.items():
            # 跳过 _aliases 等非等级 key
            if str(level_str).startswith("_"):
                continue
            level = int(level_str)
            if isinstance(entry, dict):
                cap = entry.get("cap", 0)
                chengyin = entry.get("chengyin")
            else:
                logger.error(f"词组 {category} 等级 {level} 上限必须是对象，已跳过；请在游戏设置重新保存")
                continue
            if not isinstance(chengyin, (int, float)) or isinstance(chengyin, bool):
                # 普通词条承音上限遵循游戏固定 94% 比例；显式配置优先。
                # 定音词条的查询仍使用完整 cap，不受此比例限制。
                chengyin = derive_chengyin_cap(cap)
            self._affix_caps[category][level] = {
                "cap": cap,
                "chengyin": chengyin,
            }



def parse_progression(self: GameConfigManager, data: dict) -> None:
    # ── level_configs（顶层等级配置）──
    raw_levels = data.get("level_configs") or []
    seen_levels: set[int] = set()
    for item in raw_levels:
        if not isinstance(item, dict):
            continue
        level = item.get("level")
        if not isinstance(level, int) or level in seen_levels:
            continue
        capability_keys = ("allow_chengyin", "allow_retransfer",
                           "allow_retransfer_after_chengyin", "reset_no_refund")
        invalid = [key for key in capability_keys if not isinstance(item.get(key), bool)]
        if invalid:
            logger.error(f"等级 {level} 能力字段缺失或不是布尔值：{', '.join(invalid)}；已跳过，请在游戏设置补全")
            continue
        seen_levels.add(level)
        self._level_configs.append(LevelConfig(
            level=level,
            allow_reset=item.get("allow_reset"),
            allow_chengyin=item["allow_chengyin"],
            allow_retransfer=item["allow_retransfer"],
            allow_retransfer_after_chengyin=item["allow_retransfer_after_chengyin"],
            min_material_count=item.get("min_material_count"),
            judge_resistance=item.get("judge_resistance"),
            buff_resistance=item.get("buff_resistance"),
            reset_no_refund=item["reset_no_refund"],
            tuning_stones=_parse_tuning_stones(
                item.get("tuning_stones"),
                f"level_configs[{level}].tuning_stones"),
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
            gongjue_level=item.get("gongjue_level"),
            min_chengyin_level=item.get("min_chengyin_level"),
        ))
    # 按赛季编号排序
    self._season_configs.sort(key=lambda c: c.season_number)
