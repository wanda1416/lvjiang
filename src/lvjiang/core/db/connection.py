"""SQLite 连接原语：统一的超时、WAL、关闭语义。

宿主与插件的持久化库都走短连接：每次操作用完即关。这里只统一三件所有库都必须
一致的事，业务查询一律留在各自的仓储里：

- ``timeout`` 与 ``busy_timeout``（锁冲突时等待而不是立刻失败）
- ``journal_mode=WAL``：**首次切换需要独占数据库**，所以进程内必须串行；
  ``busy_timeout`` 介入不了这一步，两个线程同时 PRAGMA 会直接报 ``locked``
- 显式关闭：``sqlite3.connect`` 的上下文管理器只提交事务、**不关连接**，
  用 ``with`` 写法很容易一条条泄漏连接
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path

#: 所有库共用同一把进程内 setup 锁：WAL 切换是全局独占操作，按库分开没有必要，
#: 也不该让两个库的初始化在同一个瞬间互相抢 ``journal_mode``。
_SETUP_LOCK = threading.Lock()

DEFAULT_TIMEOUT = 5.0


class ClosingConnection(sqlite3.Connection):
    """事务上下文退出时同时关闭连接，兼容 Python 3.13 的资源检查。"""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect_database(
    path: Path | str,
    *,
    foreign_keys: bool = False,
    row_factory: Callable | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> ClosingConnection:
    """建立一条配置好 WAL 与 ``busy_timeout`` 的短连接。"""
    conn = sqlite3.connect(str(path), timeout=timeout, factory=ClosingConnection)
    try:
        if row_factory is not None:
            conn.row_factory = row_factory
        conn.execute("PRAGMA busy_timeout=5000")
        if foreign_keys:
            conn.execute("PRAGMA foreign_keys=ON")
        with _SETUP_LOCK:
            conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        conn.close()
        raise
    return conn
