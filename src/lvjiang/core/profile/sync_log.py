"""账本的同步簿记：水位、待上行行、ack 与清理。

只服务云端同步：哪一行还没传、传到第几号、哪些行可以放弃。业务读写不经过这里，
因此本模块的查询失败只影响同步进度，不影响 Profile 数值。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from loguru import logger

from .history_sql import HISTORY_COLUMNS
from .rows import HistoryRow, PendingOp


class SyncLogMixin:
    """账本同步簿记的查询与清理。"""

    def get_max_history_id(self) -> int:
        """本地 history 当前最大 id（无历史时为 0）。"""
        conn = self._borrow_connection()
        try:
            row = conn.execute("SELECT COALESCE(MAX(local_id), 0) FROM profile_history").fetchone()
        finally:
            self._release_connection(conn)
        return int(row[0]) if row else 0

    def get_history_since(self, since_id: int, limit: int,
                         author: str = "") -> list[HistoryRow]:
        """取 id 大于水位的账本行（``set`` / ``reset`` / ``regen`` / ``meta``），按 id 升序。

        供同步上行使用。``reset`` 是本机周期语义，不上传；回放端按操作时间
        自行归零。``tick`` 是时间派生的中间物，已被物化 set 取代，也不上云。
        水位用的是本地自增 id，与远端 ``remote_id`` 是两套编号。``author``
        非空时只取该作者写的行——**每端只上传自己产生的行**，离线包复制来的
        行（带来源机器标记）、跨端回放行（带来源实例标记）与旧存量行（空标记）
        永不代传。``source`` 一并带出：上行 op 有了来源，别的设备回放后才能说清
        "这次变化因为什么"。
        """
        conn = self._borrow_connection()
        try:
            rows = conn.execute(
                f"SELECT {HISTORY_COLUMNS} FROM profile_history "
                "WHERE local_id>? AND op_type IN ('set', 'reset', 'regen', 'meta')"
                + (" AND instance_id=?" if author else "") +
                " ORDER BY local_id ASC LIMIT ?",
                ((since_id, author, limit) if author else (since_id, limit)),
            ).fetchall()
        finally:
            self._release_connection(conn)
        return [HistoryRow.from_row(r) for r in rows]

    def count_pending_push(self, since_id: int, author: str) -> int:
        """本机尚未 ack、也未放弃的 set/delta 行数。``since_id`` 不再参与判定。"""
        del since_id
        conn = self._borrow_connection()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM profile_history "
                "WHERE instance_id=? AND remote_id IS NULL "
                "AND op_type IN ('set','reset','regen','meta') "
                "AND op_id NOT LIKE 'unscoped:%'",
                (author,),
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            self._release_connection(conn)

    def max_remote_id(self) -> int:
        """已应用的最大远端序号。

        只认真正落过账的远端行（``remote_id > 0``）：``-1`` 是「放弃上传」
        标记，NULL 是还没上传的本机行，两者都不能算同步进度。
        """
        conn = self._borrow_connection()
        try:
            row = conn.execute(
                "SELECT COALESCE(MAX(remote_id), 0) FROM profile_history WHERE remote_id > 0"
            ).fetchone()
            return int(row[0]) if row and row[0] else 0
        finally:
            self._release_connection(conn)

    def max_synced_local_id(self) -> int:
        """最后一条已拿到云端编号的本地行号（恢复路径的基线推导）。"""
        conn = self._borrow_connection()
        try:
            row = conn.execute(
                "SELECT COALESCE(MAX(local_id), 0) FROM profile_history "
                "WHERE remote_id > 0").fetchone()
        finally:
            self._release_connection(conn)
        return int(row[0]) if row else 0

    def list_unacked(self, author: str, limit: int,
                     since_local_id: int = 0) -> list[PendingOp]:
        """快照水位之后的待上行行。

        ``since_local_id`` 是建快照那一刻的本地最大 local_id：快照已经覆盖了
        它之前的全部状态，只有更大的行才需要上行。没有这道门，每轮同步都会
        把所有 ``remote_id IS NULL`` 的历史行翻出来重判一遍。
        """
        conn = self._borrow_connection()
        try:
            rows = conn.execute(
                "SELECT local_id, op_id, username, type, key, new_value, value_text, "
                "op_type, event_at, source, old_value_text, sync_from "
                "FROM profile_history "
                "WHERE instance_id=? AND remote_id IS NULL "
                "AND local_id>? AND op_type IN ('set', 'reset', 'regen', 'meta') "
                "AND op_id NOT LIKE 'unscoped:%' "
                "ORDER BY local_id ASC LIMIT ?",
                (author, since_local_id, limit),
            ).fetchall()
        finally:
            self._release_connection(conn)
        return [PendingOp.from_row(row) for row in rows]

    def mark_remote_ids(self, pairs: list[tuple[int, int]]) -> None:
        """写入 ack 序号或放弃标记 ``-1``。已经有 remote_id 的行不动。"""
        if not pairs:
            return
        conn = self._borrow_connection()
        try:
            self._begin(conn)
            conn.executemany(
                "UPDATE profile_history SET remote_id=? "
                "WHERE local_id=? AND remote_id IS NULL",
                [(remote_id, history_id) for history_id, remote_id in pairs],
            )
            self._commit(conn)
        except Exception:
            self._rollback(conn)
            raise
        finally:
            self._release_connection(conn)

    def op_id_seen(self, op_id: str) -> bool:
        if not op_id:
            return False
        conn = self._borrow_connection()
        try:
            row = conn.execute(
                "SELECT 1 FROM profile_history WHERE op_id=? LIMIT 1",
                (op_id,),
            ).fetchone()
            return row is not None
        finally:
            self._release_connection(conn)

    def users_with_history_since(self, since_ts: str, author: str = "") -> set[str]:
        """event_at 晚于 ``since_ts`` 的 history 行涉及的用户集合（快照覆盖告警用）。

        ``author`` 非空时只算该作者写的行：跨端回放行同样落在这张表里，把它们
        算进来会让用户"看起来有未推送的新数据"，白白弹一次覆盖确认。
        """
        conn = self._borrow_connection()
        try:
            rows = conn.execute(
                "SELECT DISTINCT username FROM profile_history WHERE event_at > ?"
                + (" AND instance_id=?" if author else ""),
                (since_ts, author) if author else (since_ts,),
            ).fetchall()
            return {row[0] for row in rows if row[0]}
        finally:
            self._release_connection(conn)

    def cleanup_history(self, days: int = 90) -> int:
        """清理 N 天前的历史记录，返回删除行数

        只按时间删：升级前遗留的行 ``remote_id`` 恒为 NULL，若要求已 ack
        就永远清不掉，库只增不减。待上传的行本来就在天数之内（新写入）。
        """
        cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")

        conn = self._borrow_connection()
        try:
            cursor = conn.execute(
                "DELETE FROM profile_history WHERE event_at < ?",
                (cutoff,),
            )
            self._commit(conn)
            deleted = cursor.rowcount
        finally:
            self._release_connection(conn)

        if deleted > 0:
            logger.info(f"ProfileDB: 清理 {deleted} 条过期 history (>{days}天)")
        return deleted
