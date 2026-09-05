"""燕云配置模块

统一管理燕云游戏配置。

数据来源：
- config/system/yysls/game_config.yaml
"""

from .attr_loadout import (
    delete_derivation,
    get_derivation,
    get_loadout,
    save_derivation,
    save_loadout,
)
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
from .models import (
    AttrRange,
    LevelConfig,
    LevelRule,
    SeasonConfig,
    TuningStoneRule,
)
from .play_styles import (
    FULL_GRADUATION,
    delete_play_style,
    get_base_attr_profiles,
    get_play_styles,
    rename_play_style,
    save_play_style,
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
    "TuningStoneRule",
    # 游戏配置管理器
    "GameConfigManager",
    "get_game_config",
    # 属性来源的装配状态与推导上下文
    "get_loadout",
    "save_loadout",
    "get_derivation",
    "save_derivation",
    "delete_derivation",
    # 基础属性配置存储（保留 play_style API 名称以兼容已有数据）
    "FULL_GRADUATION",
    "get_base_attr_profiles",
    "get_play_styles",
    "save_play_style",
    "delete_play_style",
    "rename_play_style",
]
