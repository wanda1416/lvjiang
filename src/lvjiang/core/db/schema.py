"""SQLite schema 版本与有序迁移。

一个库的 schema 由「版本号 + 迁移函数」的有序列表描述，版本记在
``schema_version`` 表里。这里只提供三件所有库都必须一致的事：

- **版本读取与迁移在同一个写事务里**。否则两个初始化线程会读到同一个旧版本，
  先后对同一列执行 ``ALTER TABLE``，第二个必然报「duplicate column name」。
- **迁移只按版本号驱动**，不做「看起来像已经加过这一列」的启发式判断。
- **升级前备份**由调用方以回调形式给出：备份必须先于 ``BEGIN`` 执行，且只在
  确实有未应用迁移时发生。

迁移函数体内的 SQL 属于该库的历史快照，业务代码不要从这里反向依赖。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from loguru import logger


@dataclass(frozen=True)
class Migration:
    """一次 schema 变更。``version`` 必须从 1 开始连续递增。"""

    version: int
    description: str
    apply: Callable[[sqlite3.Connection], None]


#: 升级前备份：拿到「迁移前的连接」与「库文件路径」，自己决定备份落点。
BackupHook = Callable[[sqlite3.Connection, Path], None]


def read_version(conn: sqlite3.Connection) -> int:
    """读当前 schema 版本；版本表不存在视为 0（全新库）。"""
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0] or 0) if row else 0


class Schema:
    """一个库的迁移清单与版本推进。"""

    def __init__(
        self,
        migrations: Sequence[Migration],
        *,
        label: str,
        backup: BackupHook | None = None,
    ):
        ordered = tuple(sorted(migrations, key=lambda item: item.version))
        versions = [item.version for item in ordered]
        if versions != list(range(1, len(ordered) + 1)):
            raise ValueError(f"{label} 的迁移版本必须从 1 连续递增：{versions}")
        self._migrations = ordered
        self.label = label
        self._backup = backup

    @property
    def migrations(self) -> tuple[Migration, ...]:
        return self._migrations

    @property
    def current_version(self) -> int:
        return self._migrations[-1].version if self._migrations else 0

    def pending(self, version: int) -> tuple[Migration, ...]:
        """``version`` 之后仍需应用的迁移。"""
        return tuple(item for item in self._migrations if item.version > version)

    def ensure(self, path: Path, connect: Callable[[], sqlite3.Connection]) -> None:
        """把库推进到 :attr:`current_version`。

        ``connect`` 必须返回一条**新建**连接：本方法自己负责提交/回滚与关闭，
        不接受已经处于事务中的连接。
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = connect()
        try:
            # 备份要在 BEGIN 之前：不能对已持有写事务的连接调用 backup。
            previous = read_version(conn)
            if 0 < previous < self.current_version and self._backup is not None:
                self._backup(conn, path)
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS schema_version "
                    "(version INTEGER PRIMARY KEY)")
                current = read_version(conn)
                for migration in self.pending(current):
                    migration.apply(conn)
                    conn.execute(
                        "INSERT OR REPLACE INTO schema_version VALUES (?)",
                        (migration.version,))
                    logger.info(
                        f"{self.label} 迁移 v{migration.version}: "
                        f"{migration.description}")
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        finally:
            conn.close()
