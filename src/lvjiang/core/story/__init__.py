"""剧情文案库（通用工具）：多配置文件、匹配算法与双层读写。"""

from .matching import MatchResult, match, normalize, pick_answer, similarity
from .models import (
    AUTO_PLOT_DIR,
    DEFAULT_THRESHOLD,
    SETTINGS_FILE,
    StoryDoc,
    StoryEntry,
    StorySettings,
    delete_entry,
    delete_group,
    list_groups,
    load_all,
    load_group,
    load_settings,
    save_group,
    save_settings,
    upsert_entry,
)

__all__ = [
    "AUTO_PLOT_DIR",
    "DEFAULT_THRESHOLD",
    "SETTINGS_FILE",
    "MatchResult",
    "StoryDoc",
    "StoryEntry",
    "StorySettings",
    "delete_entry",
    "delete_group",
    "list_groups",
    "load_all",
    "load_group",
    "load_settings",
    "match",
    "normalize",
    "pick_answer",
    "save_group",
    "save_settings",
    "similarity",
    "upsert_entry",
]
