"""``profile_entries`` 的当前值读写。

包含读取、锁定、快照仲裁、单条/批量写入、CAS 更新与 key 重命名——凡是要改动
「某个用户某个键的当前值」的路径都在这里，写值与记账在同一个事务内完成。

与 :class:`~.repository.ProfileDB` 的契约：本 mixin 的方法通过 ``self`` 使用
``_borrow_connection/_begin/_commit/_rollback/_release_connection``，连接一旦被
外层事务借用就不再自行提交。
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from datetime import datetime

from loguru import logger

from .author import author_stamp
from .ledger import record_history, should_record
from .rows import ENTRY_COLUMNS, Entry


class EntriesMixin:
    """``profile_entries`` 的当前值读写；连接借用与事务边界由 :class:`~.repository.ProfileDB` 提供。"""

    def get_entry(self, username: str, type_: str, key: str) -> Entry | None:
        """读取单条 entry；不存在返回 ``None``。"""
        conn = self._borrow_connection()
        try:
            row = conn.execute(
                f"SELECT {ENTRY_COLUMNS} FROM profile_entries "
                "WHERE username=? AND type=? AND key=?",
                (username, type_, key),
            ).fetchone()
        finally:
            self._release_connection(conn)
        return Entry.from_row(row) if row is not None else None

    def get_all(self, username: str) -> list[Entry]:
        """读取用户全部 profile 条目（扁平列表，调用方按 type/key 自行索引）。"""
        conn = self._borrow_connection()
        try:
            rows = conn.execute(
                f"SELECT {ENTRY_COLUMNS} FROM profile_entries "
                "WHERE username=? ORDER BY type, key",
                (username,),
            ).fetchall()
        finally:
            self._release_connection(conn)
        return [Entry.from_row(row) for row in rows]

    def list_usernames(self) -> list[str]:
        """账本或条目里出现过的全部用户名（改名与通知广播用）。"""
        conn = self._borrow_connection()
        try:
            rows = conn.execute(
                "SELECT DISTINCT username FROM profile_entries").fetchall()
        finally:
            self._release_connection(conn)
        return [str(row[0]) for row in rows if row[0]]

    def entries_for_key(self, type_: str, key: str) -> list[Entry]:
        """某模型某键的全部用户条目（key 改名后重发全量基线用）。"""
        conn = self._borrow_connection()
        try:
            rows = conn.execute(
                f"SELECT {ENTRY_COLUMNS} FROM profile_entries "
                "WHERE type=? AND key=?", (type_, key),
            ).fetchall()
        finally:
            self._release_connection(conn)
        return [Entry.from_row(row) for row in rows]

    def get_index(self, username: str) -> dict[str, dict[str, Entry]]:
        """``type → key → Entry`` 索引；值仍是 dataclass，不是 dict。"""
        index: dict[str, dict[str, Entry]] = {}
        for entry in self.get_all(username):
            index.setdefault(entry.type, {})[entry.key] = entry
        return index

    # ─── 锁定 ───

    def is_locked(self, username: str, type_: str, key: str) -> bool:
        """该用户的这个 key 是否已锁定"""
        conn = self._borrow_connection()
        try:
            row = conn.execute(
                "SELECT locked FROM profile_entries "
                "WHERE username=? AND type=? AND key=?",
                (username, type_, key),
            ).fetchone()
        finally:
            self._release_connection(conn)
        return bool(row and row[0])

    def _current_locked(
        self, conn: sqlite3.Connection, username: str, type_: str, key: str,
    ) -> bool | None:
        """当前锁状态；行不存在返回 ``None``（区别于"存在但未锁"）。"""
        row = conn.execute(
            "SELECT locked FROM profile_entries "
            "WHERE username=? AND type=? AND key=?",
            (username, type_, key),
        ).fetchone()
        return None if row is None else bool(row[0])

    def _write_locked(
        self,
        conn: sqlite3.Connection,
        username: str,
        type_: str,
        key: str,
        locked: int,
        *,
        created_at: str,
        ledger_event_at: str,
        instance_id: str,
        op_id: str,
        remote_id: int | None = None,
    ) -> None:
        """把一行的锁状态写成 ``locked``，并补一条 meta 账本行。

        「只动 ``locked``、不碰 ``value`` / ``value_text`` / ``event_at``」这条约定
        的唯一实现：本地锁定、本地解锁与跨端回放都走这里。三者的差别只在入口条件
        （本地只在状态真的变化时写）与 ``created_at``：本地新建的占位行没有值，
        也不记创建时刻；回放行记真实写入时刻。

        meta 行是锁状态的传播载体（``old/new_value`` 为锁定前后 0/1），
        ``source`` 留空——锁是机制不是业务动作。
        """
        conn.execute(
            "INSERT INTO profile_entries "
            "(username, type, key, value, value_text, event_at, created_at, locked) "
            "VALUES (?, ?, ?, 0, '', '', ?, ?) "
            "ON CONFLICT(username, type, key) DO UPDATE SET locked=excluded.locked",
            (username, type_, key, created_at, locked),
        )
        record_history(
            conn,
            event_at=ledger_event_at, username=username, type_=type_, key=key,
            old_value=1 - locked, new_value=locked, op_type="meta",
            source="", instance_id=instance_id, op_id=op_id, remote_id=remote_id,
        )

    def _toggle_locked(
        self, username: str, type_: str, key: str, *, locked: int,
    ) -> bool:
        """本地切换锁状态：只在状态真的变化时写库与记账，返回是否变化。"""
        conn = self._borrow_connection()
        try:
            self._begin(conn)
            current = self._current_locked(conn, username, type_, key)
            # 锁定：已锁就不用动；从未写过的 key 要建占位行才能锁。
            # 解锁：未锁或行不存在都说明没有可解的状态。
            already = current is True if locked else current is not True
            if already:
                self._rollback(conn)
                return False
            self._write_locked(
                conn, username, type_, key, locked,
                created_at="",
                ledger_event_at=datetime.now().isoformat(timespec="seconds"),
                instance_id=author_stamp(),
                op_id=str(uuid.uuid4()),
            )
            self._commit(conn)
            return True
        except Exception:
            self._rollback(conn)
            raise
        finally:
            self._release_connection(conn)

    def set_locked(self, username: str, type_: str, key: str) -> bool:
        """锁定该用户的这个 key；返回是否发生了变化。

        从未写过的 key 也要能锁：插入一条占位行（value / value_text / event_at
        全为默认空值），锁定期间这些值不会被任何路径读取或展示。
        """
        return self._toggle_locked(username, type_, key, locked=1)

    def unlock(self, username: str, type_: str, key: str) -> bool:
        """解除锁定；返回是否发生了变化。值保持锁定时的样子。

        锁定期间的值写入被拒绝，但账本里可能有他机写过的行；解锁时**不得**用账本
        重建值——那会让值在解锁瞬间跳变（regen 会按旧锚点补算上千点），
        与「锁定期间冻结」的承诺相反。
        """
        return self._toggle_locked(username, type_, key, locked=0)

    def apply_locked(
        self,
        username: str,
        type_: str,
        key: str,
        locked: int,
        *,
        instance_id: str,
        event_at: str,
        op_id: str,
        remote_id: int | None = None,
    ) -> None:
        """回放他端的 meta 行：照抄锁状态，账本行带上远端的幂等键。

        与本地切换的区别只有两点：不做「状态是否变化」的判断（远端说了算），
        以及 ``created_at`` 记真实写入时刻。
        """
        conn = self._borrow_connection()
        try:
            self._begin(conn)
            now = datetime.now().isoformat(timespec="seconds")
            self._write_locked(
                conn, username, type_, key, 1 if locked else 0,
                created_at=now,
                ledger_event_at=event_at or now,
                instance_id=instance_id,
                op_id=op_id,
                remote_id=remote_id,
            )
            self._commit(conn)
        except Exception:
            self._rollback(conn)
            raise
        finally:
            self._release_connection(conn)

    def merge_snapshot_candidates(self, rows: list[dict]) -> int:
        """按 key 仲裁落地快照条目。一个事务，不写账本，不改 locked。"""
        from .arbitrate import canonical_event, is_strictly_newer

        applied = 0
        conn = self._borrow_connection()
        try:
            self._begin(conn)
            now = datetime.now().isoformat(timespec="seconds")
            for row in rows:
                username, type_, key = row["username"], row["type"], row["key"]
                current = conn.execute(
                    "SELECT value, value_text, event_at, op_id, locked "
                    "FROM profile_entries WHERE username=? AND type=? AND key=?",
                    (username, type_, key),
                ).fetchone()
                if current is not None and current[4]:
                    continue
                event_at = canonical_event(row.get("event_at") or "")
                op_id = str(row.get("op_id") or "")
                if current is not None and not is_strictly_newer(
                        event_at, op_id, current[2] or "", current[3] or ""):
                    continue
                value = row.get("value")
                number = float(value) if isinstance(value, (int, float)) else 0.0
                text = row.get("value_text") or ""
                locked_val = 1 if row.get("locked") else 0
                conn.execute(
                    "INSERT INTO profile_entries "
                    "(username, type, key, value, value_text, event_at, created_at, op_id, locked) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(username, type, key) DO UPDATE SET "
                    "value=excluded.value, value_text=excluded.value_text, "
                    "event_at=excluded.event_at, created_at=excluded.created_at, "
                    "op_id=excluded.op_id, locked=excluded.locked",
                    (username, type_, key, number, text, event_at, now, op_id, locked_val),
                )
                applied += 1
            self._commit(conn)
            return applied
        except Exception:
            self._rollback(conn)
            raise
        finally:
            self._release_connection(conn)

    # ─── 写入 ───

    def upsert(
        self,
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
        # 回放时用来源方记录的旧文本；None 表示按本地当前值计算（本机写入）
        history_old_value_text: str | None = None,
    ) -> None:
        """写入单条 entry（ON CONFLICT 更新，不用 INSERT OR REPLACE）

        op_type 非 None 时记录 history（内部对比 old/new 值，无变化跳过）。
        source: 变更来源描述，随 history 一并记录。
        value_text: note 模型存储文本，其他模型传默认空串。

        跨端回放必须显式传 ``instance_id``（来源实例）、``origin_local_id``
        （来源实例的 local_id）与 ``history_ts``（源写入时刻）：缺省值会把
        回放行记成本机操作，下一轮上行就代传出去。

        锁定条目直接拒绝写入（不写值、不写 history），并记 error 暴露调用方。
        锁冲突抛 sqlite3.OperationalError。
        """
        write_ts = datetime.now().isoformat(timespec="seconds")
        event_at = event_at or write_ts
        conn = self._borrow_connection()
        try:
            self._begin(conn)

            # 读取旧值（用于 history 对比与锁定判断）
            old_row = conn.execute(
                "SELECT value, value_text, locked FROM profile_entries "
                "WHERE username=? AND type=? AND key=?",
                (username, type_, key),
            ).fetchone()
            old_value = old_row[0] if old_row else None
            old_value_text = (
                history_old_value_text if history_old_value_text is not None
                else (old_row[1] if old_row and len(old_row) > 1 else ""))
            if (touch_entry and op_type != "meta"
                    and old_row and len(old_row) > 2 and old_row[2]):
                # meta 例外：它本身就是锁定/解锁指令，必须穿透守卫传播锁状态。
                self._rollback(conn)
                logger.error(
                    f"profile 写入被拒绝：{username}.{type_}.{key} 已锁定"
                    f"（来源 {source or '未标注'}）")
                return

            if touch_entry and op_type == "meta":
                # meta 只传锁状态：locked 单列更新，value/value_text/event_at
                # 一律不动——meta 的 new_value 是锁标记 0/1，写成 value 会污染当前值。
                conn.execute(
                    "INSERT INTO profile_entries "
                    "(username, type, key, value, value_text, event_at, created_at, locked) "
                    "VALUES (?, ?, ?, 0, '', '', ?, ?) "
                    "ON CONFLICT(username, type, key) DO UPDATE SET locked=excluded.locked",
                    (username, type_, key, write_ts, 1 if float(value) >= 1 else 0),
                )
            elif touch_entry:
                # 显式列更新，锁定列不在 SET 里，不会被清掉
                conn.execute(
                    "INSERT INTO profile_entries "
                    "(username, type, key, value, value_text, event_at, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(username, type, key) DO UPDATE SET "
                    "value=excluded.value, value_text=excluded.value_text, "
                    "event_at=excluded.event_at, created_at=excluded.created_at",
                    (username, type_, key, float(value), value_text, event_at, write_ts),
                )

            if op_type is not None and should_record(
                op_type, type_=type_, old_value=old_value, new_value=float(value),
                old_value_text=old_value_text, value_text=value_text,
            ):
                written = record_history(
                    conn,
                    event_at=history_ts or write_ts,
                    username=username, type_=type_, key=key,
                    old_value=old_value, new_value=float(value),
                    old_value_text=old_value_text or "", value_text=value_text,
                    op_type=op_type, delta_value=delta_value,
                    source=source, sync_from=sync_from,
                    instance_id=instance_id or author_stamp(),
                    op_id=op_id,
                    remote_id=remote_id,
                )
                if written and touch_entry:
                    conn.execute(
                        "UPDATE profile_entries SET op_id=? "
                        "WHERE username=? AND type=? AND key=?",
                        (written, username, type_, key),
                    )
            elif op_id and touch_entry:
                conn.execute(
                    "UPDATE profile_entries SET op_id=? "
                    "WHERE username=? AND type=? AND key=?",
                    (op_id, username, type_, key),
                )

            self._commit(conn)
        finally:
            self._release_connection(conn)

    def upsert_many(
        self, username: str, entries: list[tuple]
    ) -> None:
        """批量 upsert（事务包裹），用于 tick 写入

        entries 元素: (type_, key, value, event_at, op_type, delta_value[, source[, value_text[, sync_from]]])
        """
        conn = self._borrow_connection()
        try:
            self._begin(conn)

            for entry in entries:
                source = entry[6] if len(entry) > 6 else ""
                value_text = entry[7] if len(entry) > 7 else ""
                type_, key, value, event_at, op_type, delta_value = entry[:6]
                sync_from = entry[8] if len(entry) > 8 else None
                write_ts = datetime.now().isoformat(timespec="seconds")
                event_at = event_at or write_ts

                old_row = conn.execute(
                    "SELECT value, value_text, locked FROM profile_entries "
                    "WHERE username=? AND type=? AND key=?",
                    (username, type_, key),
                ).fetchone()
                old_value = old_row[0] if old_row else None
                old_value_text = old_row[1] if old_row and len(old_row) > 1 else ""
                if old_row and len(old_row) > 2 and old_row[2]:
                    # 锁定条目：整批里逐条跳过，不写值也不写 history
                    logger.error(
                        f"profile 写入被拒绝：{username}.{type_}.{key} 已锁定"
                        f"（来源 {source or '未标注'}）")
                    continue

                conn.execute(
                    "INSERT INTO profile_entries "
                    "(username, type, key, value, value_text, event_at, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(username, type, key) DO UPDATE SET "
                    "value=excluded.value, value_text=excluded.value_text, "
                    "event_at=excluded.event_at, created_at=excluded.created_at",
                    (username, type_, key, float(value), value_text, event_at, write_ts),
                )

                if op_type is not None and should_record(
                    op_type, type_=type_, old_value=old_value,
                    new_value=float(value), old_value_text=old_value_text,
                    value_text=value_text,
                ):
                    record_history(
                        conn,
                        event_at=write_ts,
                        username=username, type_=type_, key=key,
                        old_value=old_value, new_value=float(value),
                        old_value_text=old_value_text or "",
                        value_text=value_text,
                        op_type=op_type, delta_value=delta_value,
                        source=source, sync_from=sync_from,
                        instance_id=author_stamp(),
                    )

            self._commit(conn)
        finally:
            self._release_connection(conn)

    def update_if_current(
        self,
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
        expected_value_text: str | None = None,
        value_text: str = "",
    ) -> bool:
        """CAS 更新单条 entry。

        仅当当前条目的存在状态、value（或 ``value_text``）与 event_at 仍等于调用方
        读到的快照时才写入。预期条目不存在时使用原子插入；若已被其他进程先行创建，
        则仍返回冲突。返回 True 表示更新成功；False 表示状态已被其他进程修改，
        本轮调用方应放弃。

        ``expected_value_text`` 非 ``None`` 时按**文本**比对（note 模型），此时
        ``value`` 列仍写入调用方给的值（note 恒为 0）；``None`` 表示按数值比对。
        两者互斥，调用方只能选一种快照维度。
        """
        old_value = float(expected_value)
        value = float(new_value)
        write_ts = datetime.now().isoformat(timespec="seconds")
        event_at = new_updated_at or write_ts
        conn = self._borrow_connection()
        try:
            self._begin(conn)
            text_cas = expected_value_text is not None
            if expected_entry_exists and text_cas:
                # 文本维度：WHERE 里比对 COALESCE(value_text,'')，其余同数值 CAS。
                cursor = conn.execute(
                    "UPDATE profile_entries "
                    "SET value=?, value_text=?, event_at=?, created_at=? "
                    "WHERE username=? AND type=? AND key=? "
                    "AND COALESCE(value_text, '')=? AND event_at=? AND locked=0",
                    (
                        value, value_text, event_at, write_ts, username, type_, key,
                        expected_value_text, expected_updated_at,
                    ),
                )
                history_old_value = old_value
            elif expected_entry_exists:
                cursor = conn.execute(
                    "UPDATE profile_entries "
                    "SET value=?, event_at=?, created_at=? "
                    "WHERE username=? AND type=? AND key=? "
                    "AND value=? AND event_at=? AND locked=0",
                    (
                        value, event_at, write_ts, username, type_, key,
                        old_value, expected_updated_at,
                    ),
                )
                history_old_value = old_value
            else:
                cursor = conn.execute(
                    "INSERT INTO profile_entries "
                    "(username, type, key, value, value_text, event_at, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(username, type, key) DO NOTHING",
                    (username, type_, key, value, value_text, event_at, write_ts),
                )
                history_old_value = None
            if cursor.rowcount != 1:
                self._rollback(conn)
                return False

            if op_type is not None and should_record(
                op_type, type_=type_, old_value=history_old_value, new_value=value,
                old_value_text=expected_value_text or "", value_text=value_text,
            ):
                written = record_history(
                    conn,
                    # 与 entry 用同一个事件时间：同一个写入只有一个业务时刻
                    event_at=event_at,
                    username=username, type_=type_, key=key,
                    old_value=history_old_value, new_value=value,
                    old_value_text=expected_value_text or "", value_text=value_text,
                    op_type=op_type, delta_value=delta_value,
                    source=source, sync_from=sync_from,
                    instance_id=author_stamp(),
                )
                if written:
                    conn.execute(
                        "UPDATE profile_entries SET op_id=? "
                        "WHERE username=? AND type=? AND key=?",
                        (written, username, type_, key),
                    )

            self._commit(conn)
            return True
        finally:
            self._release_connection(conn)

    def rename_keys(
        self, renames: list[tuple[str, str, str]], *, save_references: Callable[[], None],
    ) -> None:
        """全用户身份重命名；SQL 变化与引用文件共用事务，不触发数值事件。

        幂等：旧 key 已不存在说明这一步做过了（崩溃后重放），跳过继续；目标 key
        已有别处的数据才算冲突。
        """
        conn = self._borrow_connection()
        try:
            self._begin(conn)
            for model, old, new in renames:
                if old == new or not new or not new.replace("_", "").isalnum():
                    raise ValueError("无效的 key 重命名")
                if not conn.execute(
                    "SELECT 1 FROM profile_entries WHERE type=? AND key=? "
                    "UNION SELECT 1 FROM profile_history WHERE type=? AND key=? LIMIT 1",
                    (model, old, model, old),
                ).fetchone():
                    continue
                if conn.execute(
                    "SELECT 1 FROM profile_entries WHERE key=? "
                    "UNION SELECT 1 FROM profile_history WHERE key=? LIMIT 1", (new, new),
                ).fetchone():
                    raise ValueError(f"目标 key {new} 已有数据或历史，不能合并")
                conn.execute(
                    "UPDATE profile_entries SET key=? WHERE type=? AND key=?", (new, model, old))
                conn.execute(
                    "UPDATE profile_history SET key=? WHERE type=? AND key=?", (new, model, old))
                # 未限定模型的旧来源，仅在身份没有歧义时更新。
                ambiguous = conn.execute(
                    "SELECT 1 FROM profile_entries WHERE key=? AND type!=? "
                    "UNION SELECT 1 FROM profile_history WHERE key=? AND type!=? LIMIT 1",
                    (old, model, old, model),
                ).fetchone()
                refs = [f"{model}:{old}"]
                if not ambiguous:
                    refs.append(old)
                for reference in refs:
                    conn.execute(
                        "UPDATE profile_history SET sync_from=? WHERE sync_from=?",
                        (f"{model}:{new}", reference))
            save_references()
            self._commit(conn)
        except BaseException:
            self._rollback(conn)
            raise
        finally:
            self._release_connection(conn)

    # ─── History 查询 ───
