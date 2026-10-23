"""游戏配置管理器

从 game_config/ 七份配置加载全部规则，提供品阶推断、词条上限查询、
词条名映射、流派注册表等功能。

数据来源：config/system/yysls/game_config/ 下的七份领域配置。
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from ....i18n import tr
from . import affix_queries, arts_queries, equipment_queries, progression_queries
from . import game_config_loader as loader
from .affix_levels import LevelRange
from .constants import DEFAULT_CHUANLV_STONE_PERCENT
from .game_config_parsers import (
    SEASON_SWITCH_HOUR as SEASON_SWITCH_HOUR,
)
from .game_config_parsers import (
    _parse_affix_upgrades as _parse_affix_upgrades,
)
from .game_config_parsers import (
    _parse_date as _parse_date,
)
from .game_config_parsers import (
    _parse_tuning_stones as _parse_tuning_stones,
)
from .game_config_parsers import (
    _positive_int as _positive_int,
)
from .game_config_parsers import (
    _stone_map as _stone_map,
)
from .game_config_parsers import (
    _stone_units as _stone_units,
)
from .game_config_parsers import (
    _string_list as _string_list,
)
from .game_config_parsers import (
    derive_chengyin_cap as derive_chengyin_cap,
)
from .game_config_parsers import (
    validate_season_configs as validate_season_configs,
)
from .models import (
    AffixUpgrade,
    LevelConfig,
    LevelRule,
    SeasonConfig,
)


class GameConfigManager:
    """属性规则管理器

    从 game_config/ 七份配置加载全部规则，提供：
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
        # 词组同名叠加方式：category → sum / max（缺省 sum）
        self._affix_stacks: dict[str, str] = {}
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
        # 套装注册表：左右两侧是独立身份；左侧额外登记两件套属性及推荐关系。
        self._equipment_sets: dict[str, dict[str, dict]] = {
            "left": {}, "right": {},
        }
        self._gongjue_bonuses: dict[str, dict[tuple[int, int], float]] = {}
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
        # 扫描装备转律后的默认冷却天数（顶层 basic_config）
        self._equipment_cooldown_days = 5
        # 冷却到期后是否保留一次逾期进度（顶层 basic_config）
        self._equipment_cooldown_carryover = True
        # 一次传律提升被喂词条普通满值的百分比（顶层 basic_config）
        self._chuanlv_stone_percent = DEFAULT_CHUANLV_STONE_PERCENT
        # 赛季配置：赛季编号 → SeasonConfig（顶层 season_configs）
        self._season_configs: list[SeasonConfig] = []
        # 基础属性名（从 _attr 字段收集：外功攻击/气血最大值/...）
        self._base_attr_names: list[str] = []
        # 首词条候选（从 base_attrs 各部位的 _first_affixes 字段收集）
        self._first_affixes: dict[str, list[str]] = {}
        # 首词条生效等级范围：部位 → 词条名 → LevelRange
        self._first_affix_levels: dict[str, dict[str, LevelRange]] = {}
        # 词条生效等级范围：词条名 → LevelRange（词组下的 _alias_levels）
        self._affix_levels: dict[str, LevelRange] = {}
        # 词条升级规则：跨过某等级时旧词条自动变成的新词条
        self._affix_upgrades: list[AffixUpgrade] = []

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
            from .game_config_files import load_game_config
            data = load_game_config()
        self._raw = data

        # 重置全部规则（_load 会在 UI 保存后重复调用，避免残留旧映射）
        self._base_rules.clear()
        self._affix_caps.clear()
        self._affix_units.clear()
        self._affix_stacks.clear()
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
        self._first_affix_levels.clear()
        self._affix_levels.clear()
        self._affix_upgrades.clear()
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
        self._equipment_sets = {"left": {}, "right": {}}
        self._gongjue_bonuses.clear()
        self._schools.clear()
        self._level_configs.clear()
        self._season_configs.clear()

        loader.parse_basic(self, data)
        loader.parse_weapons(self, data)
        loader.parse_arts(self, data)
        loader.parse_sets(self, data)
        loader.parse_affix_catalogue(self, data)
        loader.parse_base_attrs(self, data)
        loader.parse_affix_caps(self, data)
        loader.parse_progression(self, data)

    base_attr_names = property(affix_queries.base_attr_names)

    # ── 词条映射 ────────────────────────────────────────────

    resolve_affix_category = affix_queries.resolve_affix_category

    get_aliases_for_category = affix_queries.get_aliases_for_category

    get_alias_groups = affix_queries.get_alias_groups

    # ── 词条归属（固定 5 类，与词组正交） ───────────────

    get_affix_categories = affix_queries.get_affix_categories

    get_affix_category = affix_queries.get_affix_category

    get_affix_parts = affix_queries.get_affix_parts

    get_affix_aliases = affix_queries.get_affix_aliases

    get_alias_groups_for_category = affix_queries.get_alias_groups_for_category

    get_affix_names_for_alias = affix_queries.get_affix_names_for_alias

    # ── 词库类型 ────────────────────────────────────────

    get_affix_stack = affix_queries.get_affix_stack

    get_affix_pool = affix_queries.get_affix_pool

    is_dingyin_affix = affix_queries.is_dingyin_affix

    get_normal_affix_names = affix_queries.get_normal_affix_names

    get_dingyin_affix_names = affix_queries.get_dingyin_affix_names

    # ── 词条上限查询 ────────────────────────────────────────

    get_affix_caps = affix_queries.get_affix_caps

    get_affix_cap_levels = affix_queries.get_affix_cap_levels

    get_gongjue_bonus = equipment_queries.get_gongjue_bonus

    get_equipment_sets = equipment_queries.get_equipment_sets

    resolve_equipment_set = equipment_queries.resolve_equipment_set

    equipment_set_name = equipment_queries.equipment_set_name

    recommended_equipment_set = equipment_queries.recommended_equipment_set

    get_all_affix_categories = affix_queries.get_all_affix_categories

    # ── 装备基础属性查询 ────────────────────────────────────────

    get_base_attr_values = affix_queries.get_base_attr_values

    # ── 武器类型 / 流派配置 ────────────────────────

    get_weapon_types = equipment_queries.get_weapon_types

    get_type_to_group = equipment_queries.get_type_to_group

    get_group_to_part = equipment_queries.get_group_to_part

    get_first_affixes = affix_queries.get_first_affixes

    get_first_affix_level_range = affix_queries.get_first_affix_level_range

    get_affix_level_range = affix_queries.get_affix_level_range

    is_affix_available = affix_queries.is_affix_available

    get_affix_upgrades = affix_queries.get_affix_upgrades

    resolve_affix_upgrade = affix_queries.resolve_affix_upgrade

    get_weapon_wuxue_affix = equipment_queries.get_weapon_wuxue_affix

    get_all_weapon_wuxue_affixes = equipment_queries.get_all_weapon_wuxue_affixes

    infer_equipment_type_from_name = equipment_queries.infer_equipment_type_from_name

    infer_equipment_type_from_label = equipment_queries.infer_equipment_type_from_label

    get_equipment_name_series = equipment_queries.get_equipment_name_series

    infer_original_equipment_level = equipment_queries.infer_original_equipment_level

    get_wuxue_affix_names = arts_queries.get_wuxue_affix_names

    get_martial_arts = arts_queries.get_martial_arts

    get_martial_art = arts_queries.get_martial_art

    get_martial_art_weapon = arts_queries.get_martial_art_weapon

    get_martial_art_attr = arts_queries.get_martial_art_attr

    get_martial_arts_by_weapon = arts_queries.get_martial_arts_by_weapon

    check_school_weapon_consistency = arts_queries.check_school_weapon_consistency

    get_affix_names_in_category = affix_queries.get_affix_names_in_category

    get_affix_names_in_group = affix_queries.get_affix_names_in_group

    get_affix_category_parts = affix_queries.get_affix_category_parts

    get_playstyles = arts_queries.get_playstyles

    get_playstyle = arts_queries.get_playstyle

    get_playstyles_for_arts = arts_queries.get_playstyles_for_arts

    get_playstyle_dingyin = arts_queries.get_playstyle_dingyin

    get_schools = arts_queries.get_schools

    get_school_attr = arts_queries.get_school_attr

    get_transmute_pool = arts_queries.get_transmute_pool

    get_all_transmute_pools = arts_queries.get_all_transmute_pools

    # ── 等级配置 ────────────────────────────────────────────

    get_level_configs = progression_queries.get_level_configs

    get_equipment_cooldown_days = progression_queries.get_equipment_cooldown_days

    is_equipment_cooldown_carryover_enabled = progression_queries.is_equipment_cooldown_carryover_enabled

    get_chuanlv_stone_percent = progression_queries.get_chuanlv_stone_percent

    level_config_for = progression_queries.level_config_for

    # ── 赛季配置 ────────────────────────────────────────────

    get_season_configs = progression_queries.get_season_configs

    season_config_for = progression_queries.season_config_for

    gongjue_level_for = progression_queries.gongjue_level_for

    season_at = progression_queries.season_at

    current_season = progression_queries.current_season

    current_min_chengyin_level = progression_queries.current_min_chengyin_level

    can_chengyin_this_season = progression_queries.can_chengyin_this_season

    current_equip_level = progression_queries.current_equip_level

    # ── 原始数据访问与保存（UI 编辑用） ────────────────────────

    def get_raw(self) -> dict:
        """返回原始 YAML 数据的深拷贝（UI 编辑用）"""
        return copy.deepcopy(self._raw)

    def save(self, data: dict) -> None:
        """校验并写盘，然后 reload"""
        # 简单校验：确保是 dict 且有 level_configs
        if not isinstance(data, dict):
            raise ValueError(tr("数据必须是 dict"))
        validate_season_configs(data.get("season_configs"))
        # 写盘
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, sort_keys=False)
        else:
            from .game_config_files import save_game_config
            save_game_config(data)
        # 重新加载
        self._load()

    # ── 品阶推断 ────────────────────────────────────────────

    infer_quality = affix_queries.infer_quality

    infer_level_quality = affix_queries.infer_level_quality

    infer_type_by_value = affix_queries.infer_type_by_value


# ─── 全局单例 ─────────────────────────────────────────────

_instance: GameConfigManager | None = None


def get_game_config() -> GameConfigManager:
    """获取全局 GameConfigManager 单例"""
    from ....core.config.runtime_snapshot import current_snapshot, scoped_value
    if current_snapshot() is not None:
        return scoped_value(GameConfigManager, GameConfigManager)
    global _instance
    if _instance is None:
        _instance = GameConfigManager()
    return _instance


def reset_game_config() -> None:
    """重置全局 GameConfigManager 单例（测试隔离用）"""
    global _instance
    _instance = None
