"""一个数据库文件 = 连接策略 + schema 迁移清单。

仓储只通过 :class:`Database` 取连接和事务，不自己拼 ``sqlite3.connect``，也不
自己写版本循环——三处各写一遍的结果就是备份、写锁、关闭语义各自跑偏。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from .connection import ClosingConnection, connect_database
from .schema import Migration, Schema, read_version

__all__ = ["Database", "Migration", "Schema", "read_version"]


class Database:
    """短连接、WAL、按版本号迁移的 SQLite 文件。"""

    def __init__(
        self,
        path: Path | str,
        schema: Schema,
        *,
        foreign_keys: bool = False,
        row_factory: Callable | None = None,
    ):
        self.path = Path(path)
        self.schema = schema
        self._foreign_keys = foreign_keys
        self._row_factory = row_factory
        schema.ensure(self.path, self.connect)

    @property
    def current_version(self) -> int:
        return self.schema.current_version

    def connect(self) -> ClosingConnection:
        """新建一条短连接（调用方负责关闭）。"""
        return connect_database(
            self.path,
            foreign_keys=self._foreign_keys,
            row_factory=self._row_factory,
        )

    def version(self) -> int:
        conn = self.connect()
        try:
            return read_version(conn)
        finally:
            conn.close()

    @contextmanager
    def connection(self) -> Iterator[ClosingConnection]:
        """只读或单条语句：退出即关，不隐式提交。

        写入必须走 :meth:`transaction`——``sqlite3`` 的隐式事务在关闭连接时
        会被回滚，误用这里写数据会静默丢写。
        """
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[ClosingConnection]:
        """``BEGIN IMMEDIATE`` 包裹的写事务：成功提交，异常回滚，始终关闭。"""
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        finally:
            conn.close()

    def execute(self, sql: str, params: tuple = ()) -> list[tuple]:
        """一次性读取的便捷入口。"""
        with self.connection() as conn:
            return conn.execute(sql, params).fetchall()
