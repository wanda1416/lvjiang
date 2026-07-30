"""材料数据库 - 向后兼容模块

已迁移至 reference_db.py，此模块保留别名以支持渐进迁移。
新代码请使用 ReferenceDatabase / ReferenceEntry。
"""

# 直接从新模块导入所有定义
from lvjiang.core.reference_db import (
    MaterialDatabase,
    # 向后兼容别名
    MaterialEntry,
    ReferenceDatabase,
    ReferenceEntry,
)

__all__ = [
    "ReferenceEntry",
    "ReferenceDatabase",
    "MaterialEntry",      # 向后兼容
    "MaterialDatabase",   # 向后兼容
]
