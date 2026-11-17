"""图像参考库 —— 基于图库空间的参考图元数据管理。

- 数据结构与常量：:mod:`.models`
- 空间管理（有哪些、激活哪个、增删）：:class:`.spaces.ReferenceSpaces`
- 合并视图与读写：:class:`.database.ReferenceDatabase`
"""

from .database import ReferenceDatabase
from .models import (
    DEFAULT_MATCH_THRESHOLD,
    DEFAULT_SPACE,
    REFERENCE_OUTPUT_RESERVED_KEYS,
    UNGROUPED_DIR,
    MetaFieldDef,
    ReferenceEntry,
    group_dir_name,
    primary_group,
    validate_crop,
    validate_group_names,
)
from .spaces import ReferenceSpaces

__all__ = [
    "DEFAULT_MATCH_THRESHOLD",
    "DEFAULT_SPACE",
    "MetaFieldDef",
    "REFERENCE_OUTPUT_RESERVED_KEYS",
    "ReferenceDatabase",
    "ReferenceEntry",
    "ReferenceSpaces",
    "UNGROUPED_DIR",
    "group_dir_name",
    "primary_group",
    "validate_crop",
    "validate_group_names",
]
