"""剧情文案库（通用工具）：多配置文件、匹配算法与双层读写。"""

from .matching import MatchResult, match, normalize, pick_answer, similarity
from .models import (
    AUTO_PLOT_DIR,
    DEFAULT_THRESHOLD,
    StoryDoc,
    StoryEntry,
    delete_entry,
    delete_group,
    list_groups,
    load_all,
    load_group,
    save_group,
    set_group_threshold,
    upsert_entry,
)

__all__ = [
    "AUTO_PLOT_DIR",
    "DEFAULT_THRESHOLD",
    "MatchResult",
    "StoryDoc",
    "StoryEntry",
    "delete_entry",
    "delete_group",
    "list_groups",
    "load_all",
    "load_group",
    "match",
    "normalize",
    "pick_answer",
    "save_group",
    "set_group_threshold",
    "similarity",
    "upsert_entry",
]
