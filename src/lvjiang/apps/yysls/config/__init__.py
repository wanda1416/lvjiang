"""燕云配置模块

统一管理燕云游戏配置。

数据来源：
- config/system/yysls/game_config.yaml
"""

from .constants import (
    AFFIX_CATEGORY_NAMES,
    BASE_ATTR_PARTS,
    EQUIP_PART_NAMES,
    POOL_DINGYIN,
    POOL_NORMAL,
    WUXUE_CATEGORY,
    normalize_equip_part,
)
from .manager import GameConfigManager, get_game_config
from .models import AttrRange, LevelConfig, LevelRule, SeasonConfig
from .play_styles import (
    BASE_ATTR_VERSION,
    delete_play_style,
    get_play_style_version,
    get_play_styles,
    is_play_style_stale,
    rename_play_style,
    save_play_style,
    stale_play_styles,
)

__all__ = [
    # 游戏配置常量
    "AFFIX_CATEGORY_NAMES",
    "BASE_ATTR_PARTS",
    "EQUIP_PART_NAMES",
    "POOL_DINGYIN",
    "POOL_NORMAL",
    "WUXUE_CATEGORY",
    "normalize_equip_part",
    # 游戏配置模型
    "AttrRange",
    "LevelConfig",
    "LevelRule",
    "SeasonConfig",
    # 游戏配置管理器
    "GameConfigManager",
    "get_game_config",
    # 基础属性配置存储（保留 play_style API 名称以兼容已有数据）
    "BASE_ATTR_VERSION",
    "get_play_styles",
    "get_play_style_version",
    "is_play_style_stale",
    "stale_play_styles",
    "save_play_style",
    "delete_play_style",
    "rename_play_style",
]
