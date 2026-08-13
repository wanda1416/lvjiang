"""燕云配置模块

统一管理游戏配置与玩家信息元数据：
- game_config.yaml: 游戏基础配置（属性品阶、词条上限、流派等）
- profile.yaml: 玩家数据模型定义（按模型归档）

数据来源：
- config/system/yysls/game_config.yaml
- config/session/profile.yaml
"""

from .constants import (
    AFFIX_CATEGORY_NAMES,
    BASE_ATTR_PARTS,
    EQUIP_PART_NAMES,
    POOL_DINGYIN,
    POOL_NORMAL,
    WUXUE_CATEGORY,
)
from .manager import GameConfigManager, get_game_config
from .models import AttrRange, LevelConfig, LevelRule, SeasonConfig
from .profile_models import (
    ALL_MODELS,
    MODEL_LABELS,
    KeyDef,
    QuotaKeyDef,
    RegenKeyDef,
    StepDef,
    StockKeyDef,
    SyncTargetDef,
    format_sync_label,
    parse_sync_key,
    parse_sync_targets,
)
from .profile_store import (
    get_active_group,
    get_alert_history,
    get_groups,
    mark_alert,
    save_groups,
    set_active_group,
    set_alert_history,
    unmark_alert,
)
from .user_profile import (
    ProfileSchema,
    get_profile_config,
    reload_profile_config,
    save_profile_config,
)

__all__ = [
    # 游戏配置常量
    "AFFIX_CATEGORY_NAMES",
    "BASE_ATTR_PARTS",
    "EQUIP_PART_NAMES",
    "POOL_DINGYIN",
    "POOL_NORMAL",
    "WUXUE_CATEGORY",
    # 游戏配置模型
    "AttrRange",
    "LevelConfig",
    "LevelRule",
    "SeasonConfig",
    # 游戏配置管理器
    "GameConfigManager",
    "get_game_config",
    # 玩家数据模型
    "ALL_MODELS",
    "MODEL_LABELS",
    "KeyDef",
    "QuotaKeyDef",
    "RegenKeyDef",
    "StepDef",
    "StockKeyDef",
    "SyncTargetDef",
    "format_sync_label",
    "parse_sync_key",
    "parse_sync_targets",
    "ProfileSchema",
    "get_profile_config",
    "reload_profile_config",
    "save_profile_config",
    # 档案总览会话存储
    "get_groups",
    "save_groups",
    "get_active_group",
    "set_active_group",
    "get_alert_history",
    "set_alert_history",
    "mark_alert",
    "unmark_alert",
]
