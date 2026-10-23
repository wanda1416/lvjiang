"""等级、赛季与冷却策略查询；读取统一配置快照。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from .game_config_parsers import _SEASON_SWITCH_TIME
from .models import (
    LevelConfig,
    SeasonConfig,
)

if TYPE_CHECKING:
    from .manager import GameConfigManager

def get_level_configs(self: GameConfigManager) -> list[LevelConfig]:
    """等级配置列表（按等级排序的副本）"""
    return list(self._level_configs)


def get_equipment_cooldown_days(self: GameConfigManager) -> int:
    """扫描装备转律后重新计算的冷却天数，缺省为 5 天。"""
    return self._equipment_cooldown_days


def is_equipment_cooldown_carryover_enabled(self: GameConfigManager) -> bool:
    """冷却到期后是否向下一轮结转逾期进度，缺省开启。"""
    return self._equipment_cooldown_carryover


def get_chuanlv_stone_percent(self: GameConfigManager) -> float:
    """一次传律提升被喂词条普通满值的百分比，缺省 4.5。

    只喂一条词条，增量 = 该词条在目标等级的普通满值 × 本比例；估算见
    ``core/affix_cultivation.py``。
    """
    return self._chuanlv_stone_percent


def level_config_for(self: GameConfigManager, level: int) -> LevelConfig | None:
    """按等级查找配置条目（精确匹配），未找到返回 None"""
    for cfg in self._level_configs:
        if cfg.level == level:
            return cfg
    return None


def get_season_configs(self: GameConfigManager) -> list[SeasonConfig]:
    """赛季配置列表（按赛季编号排序的副本）"""
    return list(self._season_configs)


def season_config_for(self: GameConfigManager, season_number: int) -> SeasonConfig | None:
    """按赛季编号查找配置条目（精确匹配），未找到返回 None"""
    for cfg in self._season_configs:
        if cfg.season_number == season_number:
            return cfg
    return None


def gongjue_level_for(self: GameConfigManager, equip_level: int) -> int:
    """返回该装备赛季实际使用的弓玦词条等级。"""
    for cfg in self._season_configs:
        if cfg.equip_level == equip_level:
            return int(cfg.gongjue_level or equip_level)
    return int(equip_level)


def season_at(self: GameConfigManager, moment: datetime) -> SeasonConfig | None:
    """返回指定本地时间生效的赛季。

    燕云赛季固定在 05:00 切换。配置约定旧赛季 ``end_date`` 与
    新赛季 ``start_date`` 为同一天，因此生效区间为
    ``[start_date 05:00, end_date 05:00)``。
    """
    for cfg in self._season_configs:
        if not cfg.start_date or not cfg.end_date:
            continue
        start = datetime.combine(cfg.start_date, _SEASON_SWITCH_TIME)
        end = datetime.combine(cfg.end_date, _SEASON_SWITCH_TIME)
        if start <= moment < end:
            return cfg
    return None


def current_season(self: GameConfigManager) -> SeasonConfig | None:
    """获取当前本地时间生效的赛季。

    无匹配返回 None。
    """
    return self.season_at(datetime.now())


def current_min_chengyin_level(self: GameConfigManager) -> int:
    """本赛季仍可承音的最低装备等级；未配置或无生效赛季返回 0。

    低于它的装备本赛季已被抛弃：既不能原地承音，也不能承音到下一阶，
    所以任何满等级/满承音假设都不该把它算进去。
    """
    season = self.current_season()
    if season is not None and season.min_chengyin_level:
        return int(season.min_chengyin_level)
    return 0


def can_chengyin_this_season(self: GameConfigManager, level: int | None) -> bool:
    """该等级的装备本赛季还能不能承音。

    等级未知时放行：作废线是用来排除确知已过期的装备，不是给缺失数据
    判罪。
    """
    floor = self.current_min_chengyin_level()
    if not floor:
        return True
    if not isinstance(level, int) or isinstance(level, bool) or level <= 0:
        return True
    return level >= floor


def current_equip_level(self: GameConfigManager) -> int:
    """返回当前生效赛季的装备等级；无生效赛季时返回 0。

    不能用 ``season_configs[-1]`` 代替当前赛季：本地或远端配置可能
    提前加入下一赛季，此时列表最后一项尚未生效。赛季时间未生效时，
    该赛季必须视为不存在，不能回退到最高等级配置。
    """
    season = self.current_season()
    if season is not None and season.equip_level:
        return int(season.equip_level)
    return 0
