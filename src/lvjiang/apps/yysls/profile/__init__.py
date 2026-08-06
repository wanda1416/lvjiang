"""玩家档案模块

提供角色信息存储、展示配置加载、数据管理等功能。
"""
from .config import ProfileConfig, get_profile_config, reload_profile_config

__all__ = [
    "ProfileConfig",
    "get_profile_config",
    "reload_profile_config",
]
