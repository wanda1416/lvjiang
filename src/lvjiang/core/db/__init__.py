"""SQLite 连接、事务与 schema 迁移的共享原语。

这里只放所有库语义一致的部分；具体表结构、查询与业务规则留在各自的仓储模块。
"""

from .connection import ClosingConnection, connect_database
from .database import Database
from .schema import Migration, Schema, read_version

__all__ = [
    "ClosingConnection",
    "Database",
    "Migration",
    "Schema",
    "connect_database",
    "read_version",
]
