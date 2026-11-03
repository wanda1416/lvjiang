"""Profile 数据 SQLite 存储层

替代早期 user.json 中的 profile 节点（该文件已不存在），提供：
- profile_entries: 当前值（upsert 覆盖）
- profile_history: 变更历史（action/override/tick/reset；身份重命名更新引用）
- profile_key_renames: 全用户 key 重命名审计
- schema_version: 轻量版本管理，支持未来增量迁移

数据库路径: config/session/profile.db（单文件集中存储）
并发策略: WAL 模式 + busy_timeout，逐条 upsert，锁冲突时放弃本轮。
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger

from lvjiang.constants import SESSION_CONFIG_DIR

from .history_migration import migrate_structured_history
from .transactions import current_transaction

# 数据库路径
_DB_PATH = SESSION_CONFIG_DIR / "profile.db"

# SQLite 首次切换 WAL 模式需要独占数据库。进程内多个初始化线程若同时
# 执行 PRAGMA journal_mode=WAL，busy_timeout 尚未必能介入，会直接报 locked。
_connection_setup_lock = threading.Lock()


# ─── Schema 版本管理 ──────────────────────────────────────────


def _migrate_v1(conn: sqlite3.Connection) -> None:
    """初始建表: profile_entries + profile_history"""
    # 不使用 executescript：它会先隐式提交已有事务，破坏
    # _ensure_schema() 持有的 BEGIN IMMEDIATE 迁移锁。
    conn.execute("""
        CREATE TABLE profile_entries (
            username   TEXT NOT NULL,
            type       TEXT NOT NULL,
            key        TEXT NOT NULL,
            value      REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT '',
            updated_time TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (username, type, key)
        )
    """)
    conn.execute("""
        CREATE TABLE profile_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT    NOT NULL,
            username    TEXT    NOT NULL,
            type        TEXT    NOT NULL,
            key         TEXT    NOT NULL,
            old_value   REAL,
            new_value   REAL    NOT NULL,
            change_type TEXT    NOT NULL,
            detail      TEXT    DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE INDEX idx_history_user_key
            ON profile_history(username, type, key, id DESC)
    """)


def _migrate_v2(conn: sqlite3.Connection) -> None:
    """history 新增 source 列：记录变更来源（幂等，列已存在时跳过）"""
    cols = [
        row[1]
        for row in conn.execute("PRAGMA table_info(profile_history)").fetchall()
    ]
    if "source" not in cols:
        conn.execute(
            "ALTER TABLE profile_history ADD COLUMN source TEXT DEFAULT ''"
        )


def _migrate_v3(conn: sqlite3.Connection) -> None:
    """entries 新增 updated_time 列：记录 SQL 实际写入时间（幂等）。"""
    cols = [
        row[1]
        for row in conn.execute("PRAGMA table_info(profile_entries)").fetchall()
    ]
    if "updated_time" not in cols:
        try:
            conn.execute(
                "ALTER TABLE profile_entries ADD COLUMN updated_time TEXT NOT NULL DEFAULT ''"
            )
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                raise
    now_ts = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "UPDATE profile_entries SET updated_time=? WHERE updated_time=''",
        (now_ts,),
    )


def _migrate_v4(conn: sqlite3.Connection) -> None:
    """entries 新增 value_text 列：note 模型存储文本（幂等）。"""
    cols = [
        row[1]
        for row in conn.execute("PRAGMA table_info(profile_entries)").fetchall()
    ]
    if "value_text" not in cols:
        conn.execute(
            "ALTER TABLE profile_entries ADD COLUMN value_text TEXT NOT NULL DEFAULT ''"
        )


def _migrate_v5(conn: sqlite3.Connection) -> None:
    """history 新增 old_value_text/new_value_text 列：note 模型记录文本变更（幂等）。"""
    cols = [
        row[1]
        for row in conn.execute("PRAGMA table_info(profile_history)").fetchall()
    ]
    if "old_value_text" not in cols:
        conn.execute(
            "ALTER TABLE profile_history ADD COLUMN old_value_text TEXT DEFAULT ''"
        )
    if "new_value_text" not in cols:
        conn.execute(
            "ALTER TABLE profile_history ADD COLUMN new_value_text TEXT DEFAULT ''"
        )


def _migrate_v6(conn: sqlite3.Connection) -> None:
    """为跨用户按类型和 key 查看历史提供倒序索引。"""
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_history_type_key "
        "ON profile_history(type, key, id DESC)"
    )


def _migrate_v8(conn: sqlite3.Connection) -> None:
    """entries 新增 locked 列：该用户把这个 key 标记为失效（幂等）。

    锁定后引擎完全忽略该值：读取返回空、写入被拒绝、周期重置不再发生，
    所以值会永久停在锁定时的样子。锁是"用户 × key"的状态，不是定义层
    属性，因此放在 entries 行上而不是 profile.yaml。
    """
    cols = [
        row[1]
        for row in conn.execute("PRAGMA table_info(profile_entries)").fetchall()
    ]
    if "locked" not in cols:
        conn.execute(
            "ALTER TABLE profile_entries ADD COLUMN locked INTEGER NOT NULL DEFAULT 0"
        )


def _migrate_v9(conn: sqlite3.Connection) -> None:
    """把历史表改造成可同步账本，并删掉两张纯审计表。

    - 语义归一：老库分不出「增量」与「绝对值覆盖」（`profile_set` 也记成
      `action`），统一按 `set` 落库；`tick`/`reset` 是时间派生，保留原类型。
    - 新增 `device_id`：账本要能说明「哪台设备写的」，也是同步幂等键的一半。
    - 删除 `profile_history_legacy`（v7 时无法解析的原文，无业务读取）与
      `profile_key_renames`（改名审计；崩溃恢复改为幂等重放，不再需要提交标记）。

    就地 ALTER，不重建表：枚举取值由写入方保证，不靠数据库约束做校验。
    """
    cols = [row[1] for row in conn.execute("PRAGMA table_info(profile_history)").fetchall()]
    if "device_id" not in cols:
        conn.execute(
            "ALTER TABLE profile_history ADD COLUMN device_id TEXT NOT NULL DEFAULT ''")
    conn.execute(
        "UPDATE profile_history SET change_type='set' "
        "WHERE change_type IN ('action', 'override', 'undo')")
    conn.execute("DROP TABLE IF EXISTS profile_history_legacy")
    conn.execute("DROP TABLE IF EXISTS profile_key_renames")


# 有序迁移列表: (版本号, 描述, 迁移函数)
MIGRATIONS: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (1, "initial schema", _migrate_v1),
    (2, "history add source column", _migrate_v2),
    (3, "entries add updated_time column", _migrate_v3),
    (4, "entries add value_text column", _migrate_v4),
    (5, "history add old/new_value_text columns", _migrate_v5),
    (6, "history add type/key index", _migrate_v6),
    (7, "structured history and key rename audit", migrate_structured_history),
    (8, "entries add locked column", _migrate_v8),
    (9, "history becomes sync ledger; drop audit tables", _migrate_v9),
]

CURRENT_VERSION = MIGRATIONS[-1][0]


# ─── ProfileDB 核心类 ────────────────────────────────────────


class ProfileDB:
    """Profile 数据 SQLite 存储层

    - 每次操作创建短生命周期连接（避免跨线程共享）
    - WAL 模式 + busy_timeout=5000ms
    - upsert 内置 history 对比，值不变时跳过记录
    """

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._ensure_schema()

    # ─── 连接管理 ───

    def _connect(self) -> sqlite3.Connection:
        """创建短生命周期连接（WAL + busy_timeout=5000）"""
        conn = sqlite3.connect(str(self._db_path), timeout=5.0)
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            with _connection_setup_lock:
                conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            conn.close()
            raise
        return conn

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

    # ─── Schema 迁移 ───

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

    def _ensure_schema(self) -> None:
        """检测当前 schema 版本，依次执行未应用的迁移。

        版本读取和迁移必须处于同一个写事务内。否则两个初始化线程可能
        同时读到旧版本，并先后对同一列执行 ALTER TABLE。
        """
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._borrow_connection()
        try:
            # backup 必须在 BEGIN 前执行，避免对已持有写事务的连接调用 backup。
            has_version = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='schema_version'"
            ).fetchone()
            if has_version:
                version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
                # 只在确实有未应用的迁移时备份，文件名跟当前版本走，
                # 避免这次加列之后又忘了改版本号导致升级不备份。
                backup_path = self._db_path.with_suffix(f".before-v{CURRENT_VERSION}.db")
                if version and version < CURRENT_VERSION and not backup_path.exists():
                    with sqlite3.connect(backup_path) as backup:
                        conn.backup(backup)
            # 在读取版本前取得写锁。并发初始化者会在 busy_timeout 范围内
            # 等待，取得锁后重新读取已经提交的新版本，不会重复执行迁移。
            self._begin(conn)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)"
            )

            row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
            current = row[0] if row and row[0] else 0

            for version, desc, migrate_fn in MIGRATIONS:
                if version > current:
                    migrate_fn(conn)
                    conn.execute(
                        "INSERT OR REPLACE INTO schema_version VALUES (?)", (version,)
                    )
                    logger.info(f"ProfileDB 迁移 v{version}: {desc}")

            self._commit(conn)
        except Exception:
            self._rollback(conn)
            raise
        finally:
            self._release_connection(conn)

        from .key_rename import recover_rename
        recover_rename(self)

    # ─── 读取 ───

    def get_entry(self, username: str, type_: str, key: str) -> dict:
        """读取单条 entry，不存在返回空 dict

        锁定条目返回的 dict 带 ``locked: True``；未锁定条目不出现该键，
        调用方据此区分"有没有锁"，不必再查一次。
        """
        conn = self._borrow_connection()
        try:
            row = conn.execute(
                "SELECT value, value_text, updated_at, updated_time, locked "
                "FROM profile_entries WHERE username=? AND type=? AND key=?",
                (username, type_, key),
            ).fetchone()
        finally:
            self._release_connection(conn)

        if row is None:
            return {}
        entry = {"value": row[0], "value_text": row[1],
                 "updated_at": row[2], "updated_time": row[3]}
        if row[4]:
            entry["locked"] = True
        return entry

    def get_all(self, username: str) -> dict[str, dict[str, dict]]:
        """读取用户全部 profile

        Returns: {type: {key: {value, value_text, updated_at[, locked]}}}
        格式与早期 user.json 的 profile 节点兼容；只有锁定条目带 ``locked``。
        """
        conn = self._borrow_connection()
        try:
            rows = conn.execute(
                "SELECT type, key, value, value_text, updated_at, updated_time, locked "
                "FROM profile_entries WHERE username=?",
                (username,),
            ).fetchall()
        finally:
            self._release_connection(conn)

        result: dict[str, dict[str, dict]] = {}
        for type_, key, value, value_text, updated_at, updated_time, locked in rows:
            model_data = result.setdefault(type_, {})
            entry = {
                "value": value,
                "value_text": value_text,
                "updated_at": updated_at,
                "updated_time": updated_time,
            }
            if locked:
                entry["locked"] = True
            model_data[key] = entry
        return result

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

    def set_locked(self, username: str, type_: str, key: str) -> bool:
        """锁定该用户的这个 key；返回是否发生了变化。

        从未写过的 key 也要能锁：插入一条占位行（value/value_text/updated_at
        全为默认空值），锁定期间这些值不会被任何路径读取或展示。
        """
        conn = self._borrow_connection()
        try:
            self._begin(conn)
            cursor = conn.execute(
                "UPDATE profile_entries SET locked=1 "
                "WHERE username=? AND type=? AND key=? AND locked=0",
                (username, type_, key),
            )
            changed = cursor.rowcount == 1
            if not changed:
                exists = conn.execute(
                    "SELECT locked FROM profile_entries "
                    "WHERE username=? AND type=? AND key=?",
                    (username, type_, key),
                ).fetchone()
                if exists is None:
                    conn.execute(
                        "INSERT INTO profile_entries "
                        "(username, type, key, value, value_text, updated_at, updated_time, locked) "
                        "VALUES (?, ?, ?, 0, '', '', '', 1)",
                        (username, type_, key),
                    )
                    changed = True
            self._commit(conn)
            return changed
        except Exception:
            self._rollback(conn)
            raise
        finally:
            self._release_connection(conn)

    def unlock(self, username: str, type_: str, key: str) -> bool:
        """解除锁定；返回是否发生了变化。值保持锁定时的样子。"""
        conn = self._borrow_connection()
        try:
            self._begin(conn)
            cursor = conn.execute(
                "UPDATE profile_entries SET locked=0 "
                "WHERE username=? AND type=? AND key=? AND locked=1",
                (username, type_, key),
            )
            self._commit(conn)
            return cursor.rowcount == 1
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
        updated_at: str | None = None,
        change_type: str | None = None,
        delta_value: float | None = None,
        source: str = "",
        value_text: str = "",
        sync_from: str | None = None,
    ) -> None:
        """写入单条 entry（ON CONFLICT 更新，不用 INSERT OR REPLACE）

        change_type 非 None 时记录 history（内部对比 old/new 值，无变化跳过）。
        source: 变更来源描述，随 history 一并记录。
        value_text: note 模型存储文本，其他模型传默认空串。
        锁定条目直接拒绝写入（不写值、不写 history），并记 error 暴露调用方。
        锁冲突抛 sqlite3.OperationalError。
        """
        write_ts = datetime.now().isoformat(timespec="seconds")
        ts = updated_at or write_ts
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
            old_value_text = old_row[1] if old_row and len(old_row) > 1 else ""
            if old_row and len(old_row) > 2 and old_row[2]:
                self._rollback(conn)
                logger.error(
                    f"profile 写入被拒绝：{username}.{type_}.{key} 已锁定"
                    f"（来源 {source or '未标注'}）")
                return

            # upsert entry：显式列更新，锁定列不在 SET 里，不会被清掉
            conn.execute(
                "INSERT INTO profile_entries "
                "(username, type, key, value, value_text, updated_at, updated_time) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(username, type, key) DO UPDATE SET "
                "value=excluded.value, value_text=excluded.value_text, "
                "updated_at=excluded.updated_at, updated_time=excluded.updated_time",
                (username, type_, key, float(value), value_text, ts, write_ts),
            )

            # history 记录
            if change_type is not None:
                should_record = False
                if change_type in ("set", "delta", "reset"):
                    # 用户主动操作始终记录
                    should_record = True
                elif type_ == "note":
                    # note 模型对比文本值
                    if old_value_text != value_text:
                        should_record = True
                elif old_value != float(value):
                    # tick 仅在值变化时记录
                    should_record = True

                if should_record:
                    conn.execute(
                        "INSERT INTO profile_history "
                        "(ts, username, type, key, old_value, new_value, "
                        "old_value_text, new_value_text, change_type, delta_value, source, sync_from, device_id) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (write_ts, username, type_, key, old_value, float(value),
                         old_value_text or "", value_text,
                         change_type, delta_value, source, sync_from, _author_stamp()),
                    )

            self._commit(conn)
        finally:
            self._release_connection(conn)

    def upsert_many(
        self, username: str, entries: list[tuple]
    ) -> None:
        """批量 upsert（事务包裹），用于 tick 写入

        entries 元素: (type_, key, value, updated_at, change_type, delta_value[, source[, value_text[, sync_from]]])
        """
        conn = self._borrow_connection()
        try:
            self._begin(conn)

            for entry in entries:
                source = entry[6] if len(entry) > 6 else ""
                value_text = entry[7] if len(entry) > 7 else ""
                type_, key, value, updated_at, change_type, delta_value = entry[:6]
                sync_from = entry[8] if len(entry) > 8 else None
                write_ts = datetime.now().isoformat(timespec="seconds")
                ts = updated_at or write_ts

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
                    "(username, type, key, value, value_text, updated_at, updated_time) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(username, type, key) DO UPDATE SET "
                    "value=excluded.value, value_text=excluded.value_text, "
                    "updated_at=excluded.updated_at, updated_time=excluded.updated_time",
                    (username, type_, key, float(value), value_text, ts, write_ts),
                )

                if change_type is not None:
                    should_record = False
                    if change_type in ("set", "delta", "reset"):
                        should_record = True
                    elif type_ == "note":
                        if old_value_text != value_text:
                            should_record = True
                    elif old_value != float(value):
                        should_record = True

                    if should_record:
                        conn.execute(
                            "INSERT INTO profile_history "
                            "(ts, username, type, key, old_value, new_value, "
                            "old_value_text, new_value_text, change_type, delta_value, source, sync_from, device_id) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (write_ts, username, type_, key, old_value, float(value),
                             old_value_text or "", value_text,
                             change_type, delta_value, source, sync_from, _author_stamp()),
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
        change_type: str | None = None,
        delta_value: float | None = None,
        source: str = "",
        sync_from: str | None = None,
    ) -> bool:
        """CAS 更新单条 entry。

        仅当当前条目的存在状态、value 与 updated_at 仍等于调用方
        读到的快照时才写入。预期条目不存在时使用原子插入；若已被
        其他进程先行创建，则仍返回冲突。
        返回 True 表示更新成功；False 表示状态已被其他进程修改，本轮调用方应放弃。
        """
        old_value = float(expected_value)
        value = float(new_value)
        write_ts = datetime.now().isoformat(timespec="seconds")
        ts = new_updated_at or write_ts
        conn = self._borrow_connection()
        try:
            self._begin(conn)
            if expected_entry_exists:
                cursor = conn.execute(
                    "UPDATE profile_entries "
                    "SET value=?, updated_at=?, updated_time=? "
                    "WHERE username=? AND type=? AND key=? "
                    "AND value=? AND updated_at=? AND locked=0",
                    (
                        value, ts, write_ts, username, type_, key,
                        old_value, expected_updated_at,
                    ),
                )
                history_old_value: float | None = old_value
            else:
                cursor = conn.execute(
                    "INSERT INTO profile_entries "
                    "(username, type, key, value, value_text, updated_at, updated_time) "
                    "VALUES (?, ?, ?, ?, '', ?, ?) "
                    "ON CONFLICT(username, type, key) DO NOTHING",
                    (username, type_, key, value, ts, write_ts),
                )
                history_old_value = None
            if cursor.rowcount != 1:
                self._rollback(conn)
                return False

            if change_type is not None:
                should_record = False
                if change_type in ("set", "delta", "reset"):
                    should_record = True
                elif history_old_value != value:
                    should_record = True

                if should_record:
                    conn.execute(
                        "INSERT INTO profile_history "
                        "(ts, username, type, key, old_value, new_value, "
                        "change_type, delta_value, source, sync_from, device_id) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (write_ts, username, type_, key, history_old_value, value,
                         change_type, delta_value, source, sync_from, _author_stamp()),
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
        for column, value, comparator in (('source', source, '='), ('ts', since, '>='), ('ts', until, '<')):
            if value is not None:
                conditions.append(f'{column}{comparator}?')
                params.append(value)
        if change_types is not None:
            if not change_types:
                return 0.0
            conditions.append('change_type IN (' + ','.join('?' for _ in change_types) + ')')
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

    def get_history(
        self,
        username: str | None,
        type_: str | None = None,
        key: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """查询变更历史；username=None 时跨用户，按写入顺序倒序分页。"""
        where, params = self._history_filter(username, type_, key)

        conn = self._borrow_connection()
        try:
            rows = conn.execute(
                f"SELECT id, ts, username, type, key, old_value, new_value, "
                f"old_value_text, new_value_text, change_type, delta_value, source, sync_from "
                f"FROM profile_history "
                f"WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        finally:
            self._release_connection(conn)

        return [
            {
                "id": r[0], "ts": r[1], "username": r[2],
                "type": r[3], "key": r[4],
                "old_value": r[5], "new_value": r[6],
                "old_value_text": r[7] or "", "new_value_text": r[8] or "",
                "change_type": r[9], "delta_value": r[10], "source": r[11], "sync_from": r[12],
            }
            for r in rows
        ]

    def history_row_exists(self, history_id: int) -> bool:
        conn = self._borrow_connection()
        try:
            return conn.execute(
                "SELECT 1 FROM profile_history WHERE id=?", (history_id,)).fetchone() is not None
        finally:
            self._release_connection(conn)

    def get_max_history_id(self) -> int:
        """本地 history 当前最大 id（无历史时为 0）。"""
        conn = self._borrow_connection()
        try:
            row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM profile_history").fetchone()
        finally:
            self._release_connection(conn)
        return int(row[0]) if row else 0

    def get_history_since(self, since_id: int, limit: int,
                         author: str = "") -> list[dict]:
        """取 id 大于水位的账本行（只要 ``set`` / ``delta``），按 id 升序。

        供同步上行使用：``tick`` / ``reset`` 是时间派生，不上云。水位用的是
        本地自增 id，与远端 ``remote_id`` 是两套编号。``author`` 非空时只取
        该作者写的行——**每端只上传自己产生的 hid**，离线包复制来的行（带
        来源机器标记）与旧存量行（空标记）永不代传。
        """
        conn = self._borrow_connection()
        try:
            rows = conn.execute(
                "SELECT id, username, type, key, new_value, new_value_text, "
                "change_type, delta_value, ts "
                "FROM profile_history "
                "WHERE id>? AND change_type IN ('set', 'delta')"
                + (" AND device_id=?" if author else "") +
                " ORDER BY id ASC LIMIT ?",
                ((since_id, author, limit) if author else (since_id, limit)),
            ).fetchall()
        finally:
            self._release_connection(conn)
        return [
            {
                "id": r[0], "username": r[1], "type": r[2], "key": r[3],
                "new_value": r[4], "new_value_text": r[5] or "",
                "change_type": r[6], "delta_value": r[7], "ts": r[8],
            }
            for r in rows
        ]

    def get_history_record(self, history_id: int) -> dict:
        """按稳定 id 读取单条变更历史；不存在返回空 dict。"""
        conn = self._borrow_connection()
        try:
            row = conn.execute(
                "SELECT id, ts, username, type, key, old_value, new_value, "
                "old_value_text, new_value_text, change_type, delta_value, source, sync_from "
                "FROM profile_history WHERE id=?",
                (history_id,),
            ).fetchone()
        finally:
            self._release_connection(conn)
        if row is None:
            return {}
        return {
            "id": row[0], "ts": row[1], "username": row[2],
            "type": row[3], "key": row[4],
            "old_value": row[5], "new_value": row[6],
            "old_value_text": row[7] or "", "new_value_text": row[8] or "",
            "change_type": row[9], "delta_value": row[10],
            "source": row[11] or "", "sync_from": row[12],
        }

    def update_history_source(
        self, history_id: int, *, expected_source: str, new_source: str,
    ) -> bool:
        """只修改指定历史记录的来源；原值已变化时拒绝覆盖。"""
        conn = self._borrow_connection()
        try:
            cursor = conn.execute(
                "UPDATE profile_history SET source=? "
                "WHERE id=? AND COALESCE(source, '')=?",
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
        """返回相同筛选条件下的历史记录总数。"""
        where, params = self._history_filter(username, type_, key)
        conn = self._borrow_connection()
        try:
            row = conn.execute(
                f"SELECT COUNT(*) FROM profile_history WHERE {where}", params,
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            self._release_connection(conn)

    @staticmethod
    def _history_filter(
        username: str | None, type_: str | None, key: str | None,
    ) -> tuple[str, list[str]]:
        conditions: list[str] = []
        params: list[str] = []
        for column, value in (("username", username), ("type", type_), ("key", key)):
            if value is not None:
                conditions.append(f"{column} = ?")
                params.append(value)
        return " AND ".join(conditions) or "1 = 1", params

    def delete_history(self, history_id: int, username: str, type_: str, key: str) -> bool:
        """只删除明确归属的这一条历史；不回写余额，不追加删除审计。"""
        conn = self._borrow_connection()
        try:
            cursor = conn.execute(
                'DELETE FROM profile_history WHERE id=? AND username=? AND type=? AND key=?',
                (history_id, username, type_, key),
            )
            self._commit(conn)
            return cursor.rowcount == 1
        finally:
            self._release_connection(conn)

    def cleanup_history(self, days: int = 90) -> int:
        """清理 N 天前的历史记录，返回删除行数"""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")

        conn = self._borrow_connection()
        try:
            cursor = conn.execute(
                "DELETE FROM profile_history WHERE ts < ?", (cutoff,)
            )
            self._commit(conn)
            deleted = cursor.rowcount
        finally:
            self._release_connection(conn)

        if deleted > 0:
            logger.info(f"ProfileDB: 清理 {deleted} 条过期 history (>{days}天)")
        return deleted


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


_author_id_cache: str | None = None


def _author_stamp() -> str:
    """本机作者标记（模块级缓存：Android 路径涉及文件读写）。"""
    global _author_id_cache
    if _author_id_cache is None:
        from ..account.identity import device_author_id

        _author_id_cache = device_author_id()
    return _author_id_cache


def wipe_profile_data() -> None:
    """清空全部 entries 与 history（「清除本地总览数据」用，云端快照重建）。"""
    get_profile_db().wipe()


def db_read_entry(username: str, type_: str, key: str) -> dict:
    return get_profile_db().get_entry(username, type_, key)


def db_read_all(username: str) -> dict[str, dict[str, dict]]:
    return get_profile_db().get_all(username)


def db_is_locked(username: str, type_: str, key: str) -> bool:
    return get_profile_db().is_locked(username, type_, key)


def db_set_locked(username: str, type_: str, key: str) -> bool:
    return get_profile_db().set_locked(username, type_, key)


def db_unlock(username: str, type_: str, key: str) -> bool:
    return get_profile_db().unlock(username, type_, key)


def db_upsert(
    username: str,
    type_: str,
    key: str,
    value: float | int,
    updated_at: str | None = None,
    change_type: str | None = None,
    delta_value: float | None = None,
    source: str = "",
    value_text: str = "",
    sync_from: str | None = None,
) -> None:
    get_profile_db().upsert(username, type_, key, value, updated_at, change_type, delta_value, source, value_text, sync_from)


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
    change_type: str | None = None,
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
        change_type=change_type,
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
) -> list[dict]:
    return get_profile_db().get_history(username, type_, key, limit, offset)


def db_get_history_record(history_id: int) -> dict:
    return get_profile_db().get_history_record(history_id)


def db_get_history_since(since_id: int, limit: int = 200,
                         author: str = "") -> list[dict]:
    """本地水位之后待上行的账本行（``set`` / ``delta``，按 id 升序）。"""
    return get_profile_db().get_history_since(since_id, limit, author=author)


def db_history_row_exists(history_id: int) -> bool:
    """本地 history 是否已有该 id 的行（同步回声护栏用）。"""
    return get_profile_db().history_row_exists(history_id)


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
    return get_profile_db().count_history(username, type_, key)


def db_cleanup_history(days: int = 90) -> int:
    return get_profile_db().cleanup_history(days)
