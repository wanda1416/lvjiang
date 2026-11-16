"""变更账本：写入一行记录，以及历史的筛选、聚合与逐条维护。

``profile_history`` 既是本机操作日志也是跨端回放账本，所以「写一行」和「按条件
读」必须共用同一套列口径——列清单来自 :mod:`.rows`，SQL 条件来自
:mod:`.history_sql`，这里只负责执行。
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime

from .history_query import HistoryQuery
from .history_sql import (
    DELTA_AMOUNT_SQL,
    ESCAPE,
    FACET_COLUMNS,
    HISTORY_COLUMNS,
    HISTORY_ORDERS,
    history_where,
    like_pattern,
    single_history_query,
)
from .rows import HistoryRow


def should_record(
    op_type: str | None,
    *,
    type_: str,
    old_value: float | None,
    new_value: float,
    old_value_text: str = "",
    value_text: str = "",
) -> bool:
    """这一次写入要不要落一行账本。

    ``op_type`` 为空表示「只写条目、不记账」（回放、快照合并等路径）。

    规则只有三条，别再在调用方各写一份：

    - 用户主动操作（``set`` / ``delta`` / ``reset``）**一律记账**——包括「值没
      变」的情况，那是用户意图的一部分；
    - note 模型比较**文本**：写回同一段文本不产生新行；
    - 其余系统行（``regen`` / ``meta``）只在值确实变化时记，否则账本会被每秒
      一次的空转填满。
    """
    if op_type is None:
        return False
    if op_type in ("set", "delta", "reset"):
        return True
    if type_ == "note":
        return (old_value_text or "") != (value_text or "")
    return old_value != new_value


def record_history(
    conn: sqlite3.Connection,
    *,
    event_at: str,
    username: str,
    type_: str,
    key: str,
    old_value: float | None,
    new_value: float,
    old_value_text: str = "",
    value_text: str = "",
    op_type: str,
    delta_value: float | None = None,
    source: str = "",
    sync_from: str | None = None,
    instance_id: str,
    op_id: str | None = None,
    remote_id: int | None = None,
) -> str | None:
    """写一行账本记录。``op_id`` 已存在时跳过。

    本机写入不传 ``op_id``，这里生成 uuid。跨端回放传入云端的 ``op_id``，
    并带上已经分配的 ``remote_id``，这样回放行不会再次进入待上传集合。

    返回新行的 ``op_id``；冲突时返回 None。
    """
    identity = op_id or str(uuid.uuid4())
    cursor = conn.execute(
        "INSERT INTO profile_history "
        "(event_at, username, type, key, old_value, new_value, "
        "old_value_text, value_text, op_type, delta_value, source, "
        "sync_from, instance_id, op_id, remote_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(op_id) DO NOTHING",
        (event_at, username, type_, key, old_value, float(new_value),
         old_value_text or "", value_text or "",
         op_type, delta_value, source or "", sync_from or "", instance_id,
         identity, remote_id,
         # created_at：真实写入时刻（审计用，不参与任何裁决）
         datetime.now().isoformat(timespec="seconds")),
    )
    if not cursor.rowcount:
        return None
    return identity


class LedgerMixin:
    """``profile_history`` 的行写入与历史查询。"""

    def remote_id_of_op(self, op_id: str) -> int | None:
        """该账本行的到达序；本机待上传的行（``remote_id IS NULL``）返回 ``None``。

        回放同一秒里的两条写入要靠它分先后，见
        :func:`~.arbitrate.is_newer_write`。只有 ``op_id`` 是条目的锚点身份，
        ``remote_id`` 才是「云端第几条到达」。
        """
        if not op_id:
            return None
        conn = self._borrow_connection()
        try:
            row = conn.execute(
                "SELECT remote_id FROM profile_history WHERE op_id=? AND op_id<>''",
                (op_id,),
            ).fetchone()
        finally:
            self._release_connection(conn)
        if row is None or row[0] is None:
            return None
        return int(row[0])

    def aggregate_history(
        self, username: str, type_: str, key: str, *, operation: str = 'sum',
        source: str | None = None, change_types: list[str] | None = None,
        direction: str = 'all', since: str | None = None, until: str | None = None,
    ) -> float:
        """按当前连接聚合实际变动量；事务内可见尚未提交的关联写入。"""
        operations = {'sum': 'SUM', 'count': 'COUNT', 'min': 'MIN', 'max': 'MAX', 'avg': 'AVG'}
        if operation not in operations:
            raise ValueError(f'不支持的历史聚合方式: {operation}')
        if direction not in {'all', 'pos', 'neg'}:
            raise ValueError(f'不支持的历史变动方向: {direction}')
        conditions = ['username=?', 'type=?', 'key=?']
        params: list[object] = [username, type_, key]
        # 老历史可能未记录 delta；由已有旧新值计算，不写回历史。
        amount = 'COALESCE(delta_value, new_value - COALESCE(old_value, 0))'
        for column, value, comparator in (('source', source, '='), ('event_at', since, '>='), ('event_at', until, '<')):
            if value is not None:
                conditions.append(f'{column}{comparator}?')
                params.append(value)
        if change_types is not None:
            if not change_types:
                return 0.0
            conditions.append('op_type IN (' + ','.join('?' for _ in change_types) + ')')
            params.extend(change_types)
        if direction != 'all':
            conditions.append(amount + (' > 0' if direction == 'pos' else ' < 0'))
        conn = self._borrow_connection()
        try:
            row = conn.execute(
                f'SELECT {operations[operation]}({amount}) FROM profile_history WHERE ' + ' AND '.join(conditions),
                params,
            ).fetchone()
            return float(row[0] or 0)
        finally:
            self._release_connection(conn)

    @staticmethod
    def _history_row(row: tuple) -> HistoryRow:
        """历史行 → :class:`HistoryRow`；列顺序由 ``HISTORY_COLUMNS`` 决定。"""
        return HistoryRow.from_row(row)

    def get_history(
        self,
        username: str | None,
        type_: str | None = None,
        key: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[HistoryRow]:
        """单用户 / 单键的便捷查询，内部走 ``history_page``；复杂条件用后者。"""
        query = single_history_query(username, type_, key)
        return self.history_page(query, limit=limit, offset=offset)

    def history_page(
        self,
        query: HistoryQuery,
        limit: int = 100,
        offset: int = 0,
        order: str = "id_desc",
    ) -> list[HistoryRow]:
        """按条件分页取变更历史。"""
        where, params = history_where(query)
        ordering = HISTORY_ORDERS.get(order, HISTORY_ORDERS["id_desc"])

        conn = self._borrow_connection()
        try:
            rows = conn.execute(
                f"SELECT {HISTORY_COLUMNS} FROM profile_history "
                f"WHERE {where} ORDER BY {ordering} LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        finally:
            self._release_connection(conn)

        return [self._history_row(r) for r in rows]

    def history_count(self, query: HistoryQuery) -> int:
        """相同条件的历史记录总数（分页用）。"""
        where, params = history_where(query)
        conn = self._borrow_connection()
        try:
            row = conn.execute(
                f"SELECT COUNT(*) FROM profile_history WHERE {where}", params,
            ).fetchone()
        finally:
            self._release_connection(conn)
        return int(row[0]) if row else 0

    def history_facets(
        self,
        query: HistoryQuery,
        dimension: str,
        limit: int = 10,
        search: str = "",
    ) -> list[tuple[str, int]]:
        """某个维度的聚合候选（按出现次数倒序，最多 ``limit`` 项）。

        计数用的是"去掉本维度条件"的其它条件（见 ``HistoryQuery.relaxed``），
        这样勾选一个值之后候选列表不会塌缩成只剩它自己。``search`` 是候选
        列表内的模糊查找，不是查询条件。
        """
        expr = FACET_COLUMNS[dimension]
        where, params = history_where(query.relaxed(dimension))
        if search:
            where = f"({where}) AND {expr} LIKE ? {ESCAPE}"
            params.append(like_pattern(search))
        params.append(int(limit))

        conn = self._borrow_connection()
        try:
            rows = conn.execute(
                f"SELECT {expr} AS facet_value, COUNT(*) AS total "
                f"FROM profile_history WHERE {where} "
                f"GROUP BY facet_value ORDER BY total DESC, facet_value ASC LIMIT ?",
                params,
            ).fetchall()
        finally:
            self._release_connection(conn)
        return [(r[0] or "", int(r[1])) for r in rows]

    def history_facet_total(
        self, query: HistoryQuery, dimension: str, search: str = "",
    ) -> int:
        """该维度在相同条件下的取值个数，用于提示"另有 N 项需输入搜索"。"""
        expr = FACET_COLUMNS[dimension]
        where, params = history_where(query.relaxed(dimension))
        if search:
            where = f"({where}) AND {expr} LIKE ? {ESCAPE}"
            params.append(like_pattern(search))
        conn = self._borrow_connection()
        try:
            row = conn.execute(
                f"SELECT COUNT(DISTINCT {expr}) FROM profile_history WHERE {where}",
                params,
            ).fetchone()
        finally:
            self._release_connection(conn)
        return int(row[0]) if row else 0

    def history_delta_totals(self, query: HistoryQuery) -> tuple[float, float]:
        """按当前条件统计的正向 / 负向变动量合计（对话框摘要行）。"""
        where, params = history_where(query)
        conn = self._borrow_connection()
        try:
            row = conn.execute(
                "SELECT "
                "COALESCE(SUM(CASE WHEN amount > 0 THEN amount END), 0), "
                "COALESCE(SUM(CASE WHEN amount < 0 THEN amount END), 0) "
                f"FROM (SELECT {DELTA_AMOUNT_SQL} AS amount "
                f"FROM profile_history WHERE {where})",
                params,
            ).fetchone()
        finally:
            self._release_connection(conn)
        return (float(row[0]) if row else 0.0, float(row[1]) if row else 0.0)

    def get_history_record(self, history_id: int) -> HistoryRow | None:
        """按稳定 id 读取单条变更历史；不存在返回空 dict。"""
        conn = self._borrow_connection()
        try:
            row = conn.execute(
                f"SELECT {HISTORY_COLUMNS} FROM profile_history WHERE local_id=?",
                (history_id,),
            ).fetchone()
        finally:
            self._release_connection(conn)
        return self._history_row(row) if row is not None else None

    def update_history_source(
        self, history_id: int, *, expected_source: str, new_source: str,
    ) -> bool:
        """只修改指定历史记录的来源；原值已变化时拒绝覆盖。"""
        conn = self._borrow_connection()
        try:
            cursor = conn.execute(
                "UPDATE profile_history SET source=? "
                "WHERE local_id=? AND COALESCE(source, '')=?",
                (new_source, history_id, expected_source),
            )
            self._commit(conn)
            return cursor.rowcount == 1
        finally:
            self._release_connection(conn)

    def count_history(
        self,
        username: str | None,
        type_: str | None = None,
        key: str | None = None,
    ) -> int:
        """单用户 / 单键的便捷计数，内部走 ``history_count``。"""
        return self.history_count(single_history_query(username, type_, key))

    def delete_history(self, history_id: int, username: str, type_: str, key: str) -> bool:
        """只删除明确归属的这一条历史；不回写余额，不追加删除审计。"""
        conn = self._borrow_connection()
        try:
            cursor = conn.execute(
                'DELETE FROM profile_history WHERE local_id=? AND username=? AND type=? AND key=?',
                (history_id, username, type_, key),
            )
            self._commit(conn)
            return cursor.rowcount == 1
        finally:
            self._release_connection(conn)
