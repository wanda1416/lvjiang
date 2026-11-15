"""Profile 数据存储层的组合根

把 ``profile.db`` 的 schema（:mod:`.migrations`）挂到共享的 SQLite 原语
（:class:`lvjiang.core.db.Database`）上，并把三组能力组合成一个仓储：

- :class:`~.entries.EntriesMixin`：``profile_entries`` 当前值读写
- :class:`~.ledger.LedgerMixin`：``profile_history`` 账本写入与历史查询
- :class:`~.sync_log.SyncLogMixin`：跨端同步的簿记查询

本模块自己只管连接借用（外层事务可接管连接）、清空、单例与模块级便捷函数。

数据库路径: config/session/profile.db（单文件集中存储）
并发策略: WAL 模式 + busy_timeout，逐条 upsert，锁冲突时放弃本轮。
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from lvjiang.constants import SESSION_CONFIG_DIR

from ..db import Database, Migration, Schema
from .author import author_stamp
from .entries import EntriesMixin
from .history_query import HistoryQuery
from .ledger import LedgerMixin, record_history
from .migrations import (
    CURRENT_VERSION,  # noqa: F401  转出口：测试与发布校验按旧路径读
    MIGRATIONS,
    backup_before_upgrade,
)
from .rows import Entry, HistoryRow, PendingOp
from .sync_log import SyncLogMixin
from .transactions import current_transaction

# 数据库路径
_DB_PATH = SESSION_CONFIG_DIR / "profile.db"

#: 兼容别名：跨模块导入点与测试补丁（``snapshot_sync.repository._author_stamp``）
#: 仍按下划线名取作者标记；实现见 :mod:`.author`。
_author_stamp = author_stamp


def _schema() -> Schema:
    """按当前 ``MIGRATIONS`` 构造 schema。

    每次构造都重新读取模块级 ``MIGRATIONS``：测试靠替换它来模拟「停在旧版本的
    库」，预构建成模块常量会让那种替换失效。
    """
    return Schema(
        [Migration(*row) for row in MIGRATIONS],
        label="ProfileDB",
        backup=backup_before_upgrade,
    )


class ProfileDB(EntriesMixin, LedgerMixin, SyncLogMixin):
    """Profile 数据存储层：连接借用、清空与三组能力的组合。

    - 每次操作创建短生命周期连接（避免跨线程共享），连接策略来自 ``core.db``
    - schema 版本与逐版迁移来自 :mod:`.migrations`
    - 方法体分布见各 mixin 模块；这里只保留连接借用与清空
    """

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._db = Database(db_path, _schema())
        from .key_rename import recover_rename

        recover_rename(self)

    # ─── 连接管理 ───

    def _connect(self) -> sqlite3.Connection:
        """创建短生命周期连接（WAL + busy_timeout=5000）。"""
        return self._db.connect()

    def _shared_connection(self, conn: sqlite3.Connection) -> bool:
        active = current_transaction()
        return active is not None and active.db is self and active.connection is conn

    def _borrow_connection(self) -> sqlite3.Connection:
        active = current_transaction()
        if active is not None and active.db is self:
            return active.connection
        return self._connect()

    def _begin(self, conn: sqlite3.Connection) -> None:
        if not self._shared_connection(conn):
            conn.execute('BEGIN IMMEDIATE')

    def _commit(self, conn: sqlite3.Connection) -> None:
        if not self._shared_connection(conn):
            conn.commit()

    def _rollback(self, conn: sqlite3.Connection) -> None:
        if not self._shared_connection(conn):
            conn.rollback()

    def _release_connection(self, conn: sqlite3.Connection) -> None:
        if not self._shared_connection(conn):
            conn.close()

    # ─── 清空 ───

    def wipe(self) -> None:
        """清空全部 entries 与 history（不清 schema）。

        「清除本地总览数据」用：与 reset_profile_db（模块级，只重置单例、
        不删数据）不同，这里真的删行，删除后由云端快照 bootstrap 重建。
        """
        conn = self._borrow_connection()
        try:
            conn.execute("DELETE FROM profile_entries")
            conn.execute("DELETE FROM profile_history")
            conn.commit()
        finally:
            self._release_connection(conn)


    # ─── 读取 ───


# ─── 模块级单例与便捷函数 ────────────────────────────────────


_db: ProfileDB | None = None
_db_lock = threading.Lock()


def get_profile_db() -> ProfileDB:
    """线程安全地懒加载 ProfileDB 单例。"""
    global _db
    if _db is None:
        with _db_lock:
            if _db is None:
                _db = ProfileDB(_DB_PATH)
    return _db


def reset_profile_db() -> None:
    """重置单例（测试用）"""
    global _db
    with _db_lock:
        _db = None


def wipe_profile_data() -> None:
    """清空全部 entries 与 history（「清除本地总览数据」用，云端快照重建）。"""
    get_profile_db().wipe()


def db_read_entry(username: str, type_: str, key: str) -> Entry | None:
    return get_profile_db().get_entry(username, type_, key)


def db_read_all(username: str) -> list[Entry]:
    return get_profile_db().get_all(username)


def db_read_index(username: str) -> dict[str, dict[str, Entry]]:
    """``type → key → Entry`` 索引（UI / 引擎按 type+key 取单条时用）。"""
    return get_profile_db().get_index(username)


def db_is_locked(username: str, type_: str, key: str) -> bool:
    return get_profile_db().is_locked(username, type_, key)


def db_set_locked(username: str, type_: str, key: str) -> bool:
    return get_profile_db().set_locked(username, type_, key)


def db_unlock(username: str, type_: str, key: str) -> bool:
    return get_profile_db().unlock(username, type_, key)


def db_merge_snapshot_candidates(rows: list[dict]) -> int:
    return get_profile_db().merge_snapshot_candidates(rows)


def db_upsert(
    username: str,
    type_: str,
    key: str,
    value: float | int,
    event_at: str | None = None,
    op_type: str | None = None,
    delta_value: float | None = None,
    source: str = "",
    value_text: str = "",
    sync_from: str | None = None,
    *,
    instance_id: str | None = None,
    history_ts: str | None = None,
    op_id: str | None = None,
    remote_id: int | None = None,
    touch_entry: bool = True,
    history_old_value_text: str | None = None,
) -> None:
    get_profile_db().upsert(
        username, type_, key, value, event_at, op_type, delta_value,
        source, value_text, sync_from,
        instance_id=instance_id,
        history_ts=history_ts, op_id=op_id, remote_id=remote_id,
        touch_entry=touch_entry,
        history_old_value_text=history_old_value_text)


def db_upsert_many(username: str, entries: list[tuple]) -> None:
    get_profile_db().upsert_many(username, entries)


def db_update_if_current(
    username: str,
    type_: str,
    key: str,
    *,
    expected_value: float | int,
    expected_updated_at: str,
    expected_entry_exists: bool = True,
    new_value: float | int,
    new_updated_at: str | None = None,
    op_type: str | None = None,
    delta_value: float | None = None,
    source: str = "",
    sync_from: str | None = None,
) -> bool:
    return get_profile_db().update_if_current(
        username,
        type_,
        key,
        expected_value=expected_value,
        expected_updated_at=expected_updated_at,
        expected_entry_exists=expected_entry_exists,
        new_value=new_value,
        new_updated_at=new_updated_at,
        op_type=op_type,
        delta_value=delta_value,
        sync_from=sync_from,
        source=source,
    )


def db_get_history(
    username: str | None,
    type_: str | None = None,
    key: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[HistoryRow]:
    """单用户 / 单键的便捷查询；多条件查询用 ``db_history_page``。"""
    return get_profile_db().get_history(username, type_, key, limit, offset)


def db_history_page(
    query: HistoryQuery,
    limit: int = 100,
    offset: int = 0,
    order: str = "id_desc",
) -> list[HistoryRow]:
    return get_profile_db().history_page(query, limit, offset, order)


def db_history_count(query: HistoryQuery) -> int:
    return get_profile_db().history_count(query)


def db_history_facets(
    query: HistoryQuery,
    dimension: str,
    limit: int = 10,
    search: str = "",
) -> list[tuple[str, int]]:
    """某维度的聚合候选 ``[(取值, 条数)]``，按条数倒序。"""
    return get_profile_db().history_facets(query, dimension, limit, search)


def db_history_facet_total(
    query: HistoryQuery, dimension: str, search: str = "",
) -> int:
    return get_profile_db().history_facet_total(query, dimension, search)


def db_history_delta_totals(query: HistoryQuery) -> tuple[float, float]:
    """当前条件下的正向 / 负向变动量合计。"""
    return get_profile_db().history_delta_totals(query)


def db_get_history_record(history_id: int) -> HistoryRow | None:
    return get_profile_db().get_history_record(history_id)


def db_get_history_since(since_id: int, limit: int = 200,
                         author: str = "") -> list[HistoryRow]:
    """本地水位之后待上行的账本行（``set`` / ``delta``，按 id 升序）。"""
    return get_profile_db().get_history_since(since_id, limit, author=author)


def db_apply_meta_locked(username: str, type_: str, key: str, locked: int,
                         *, instance_id: str, event_at: str, op_id: str,
                         remote_id: int | None) -> None:
    """回放他端的 meta 行：只改 locked，不碰 value/event_at（v2 §2.3）。

    条目不存在时插占位行（与本地锁定行为一致）。账本行照记（op_type=meta），
    remote_id 随行写入，避免回声。
    """
    db = get_profile_db()
    conn = db._borrow_connection()
    try:
        db._begin(conn)
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO profile_entries "
            "(username, type, key, value, value_text, event_at, created_at, locked) "
            "VALUES (?, ?, ?, 0, '', '', ?, ?) "
            "ON CONFLICT(username, type, key) DO UPDATE SET locked=excluded.locked",
            (username, type_, key, now, locked),
        )
        record_history(
            conn,
            event_at=event_at or now, username=username, type_=type_, key=key,
            old_value=1 - locked, new_value=locked, op_type="meta",
            source="", instance_id=instance_id, op_id=op_id, remote_id=remote_id,
        )
        db._commit(conn)
    except Exception:
        db._rollback(conn)
        raise
    finally:
        db._release_connection(conn)


def db_max_synced_local_id() -> int:
    """账本里最后一条已知云端编号的行（local_id）。

    恢复路径用：account 同步状态丢失时，「local_id > 该值 且 remote_id IS NULL」
    就是基线之后的待上行行——引导回放的行带 remote_id 落在账本尾部，
    天然把存量旧行挡在基线之下，无需任何标记。
    """
    return get_profile_db().max_synced_local_id()


def db_max_history_id() -> int:
    """本地 history 最大 id。"""
    return get_profile_db().get_max_history_id()


def db_update_history_source(
    history_id: int, *, expected_source: str, new_source: str,
) -> bool:
    return get_profile_db().update_history_source(
        history_id, expected_source=expected_source, new_source=new_source,
    )


def db_count_history(
    username: str | None,
    type_: str | None = None,
    key: str | None = None,
) -> int:
    """单用户 / 单键的便捷计数；多条件计数用 ``db_history_count``。"""
    return get_profile_db().count_history(username, type_, key)


def db_users_with_history_since(since_ts: str, author: str = "") -> set[str]:
    return get_profile_db().users_with_history_since(since_ts, author)


def db_count_pending_push(since_id: int, author: str) -> int:
    return get_profile_db().count_pending_push(since_id, author)


def db_list_unacked(author: str, limit: int = 200,
                    since_local_id: int = 0) -> list[PendingOp]:
    return get_profile_db().list_unacked(author, limit, since_local_id)


def db_max_remote_id() -> int:
    """本机账本里已应用的最大远端序号（同步状态丢失时用它恢复水位）。"""
    return get_profile_db().max_remote_id()


def db_mark_remote_ids(pairs: list[tuple[int, int]]) -> None:
    get_profile_db().mark_remote_ids(pairs)


def db_op_id_seen(op_id: str) -> bool:
    return get_profile_db().op_id_seen(op_id)


def db_cleanup_history(days: int = 90) -> int:
    return get_profile_db().cleanup_history(days)





