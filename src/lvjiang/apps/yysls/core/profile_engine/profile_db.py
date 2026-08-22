"""Profile 数据 SQLite 存储层

替代 user.json 中的 profile 节点，提供：
- profile_entries: 当前值（upsert 覆盖）
- profile_history: 变更历史（append-only，记录 action/manual/tick 三类变更）
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


# 有序迁移列表: (版本号, 描述, 迁移函数)
MIGRATIONS: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (1, "initial schema", _migrate_v1),
    (2, "history add source column", _migrate_v2),
    (3, "entries add updated_time column", _migrate_v3),
    (4, "entries add value_text column", _migrate_v4),
    (5, "history add old/new_value_text columns", _migrate_v5),
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

    # ─── Schema 迁移 ───

    def _ensure_schema(self) -> None:
        """检测当前 schema 版本，依次执行未应用的迁移。

        版本读取和迁移必须处于同一个写事务内。否则两个初始化线程可能
        同时读到旧版本，并先后对同一列执行 ALTER TABLE。
        """
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            # 在读取版本前取得写锁。并发初始化者会在 busy_timeout 范围内
            # 等待，取得锁后重新读取已经提交的新版本，不会重复执行迁移。
            conn.execute("BEGIN IMMEDIATE")
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

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ─── 读取 ───

    def get_entry(self, username: str, type_: str, key: str) -> dict:
        """读取单条 entry，不存在返回空 dict"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT value, value_text, updated_at, updated_time FROM profile_entries "
                "WHERE username=? AND type=? AND key=?",
                (username, type_, key),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return {}
        return {"value": row[0], "value_text": row[1], "updated_at": row[2], "updated_time": row[3]}

    def get_all(self, username: str) -> dict[str, dict[str, dict]]:
        """读取用户全部 profile

        Returns: {type: {key: {value, value_text, updated_at}}}
        格式与 user.json 的 profile 节点兼容。
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT type, key, value, value_text, updated_at, updated_time FROM profile_entries "
                "WHERE username=?",
                (username,),
            ).fetchall()
        finally:
            conn.close()

        result: dict[str, dict[str, dict]] = {}
        for type_, key, value, value_text, updated_at, updated_time in rows:
            model_data = result.setdefault(type_, {})
            model_data[key] = {
                "value": value,
                "value_text": value_text,
                "updated_at": updated_at,
                "updated_time": updated_time,
            }
        return result

    # ─── 写入 ───

    def upsert(
        self,
        username: str,
        type_: str,
        key: str,
        value: float | int,
        updated_at: str | None = None,
        change_type: str | None = None,
        detail: str = "",
        source: str = "",
        value_text: str = "",
    ) -> None:
        """INSERT OR REPLACE 单条 entry

        change_type 非 None 时记录 history（内部对比 old/new 值，无变化跳过）。
        source: 变更来源描述，随 history 一并记录。
        value_text: note 模型存储文本，其他模型传默认空串。
        锁冲突抛 sqlite3.OperationalError。
        """
        write_ts = datetime.now().isoformat(timespec="seconds")
        ts = updated_at or write_ts
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")

            # 读取旧值（用于 history 对比）
            old_row = conn.execute(
                "SELECT value, value_text FROM profile_entries WHERE username=? AND type=? AND key=?",
                (username, type_, key),
            ).fetchone()
            old_value = old_row[0] if old_row else None
            old_value_text = old_row[1] if old_row and len(old_row) > 1 else ""

            # upsert entry
            conn.execute(
                "INSERT OR REPLACE INTO profile_entries "
                "(username, type, key, value, value_text, updated_at, updated_time) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (username, type_, key, float(value), value_text, ts, write_ts),
            )

            # history 记录
            if change_type is not None:
                should_record = False
                if change_type in ("action", "override"):
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
                        "old_value_text, new_value_text, change_type, detail, source) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (write_ts, username, type_, key, old_value, float(value),
                         old_value_text or "", value_text,
                         change_type, detail, source),
                    )

            conn.commit()
        finally:
            conn.close()

    def upsert_many(
        self, username: str, entries: list[tuple]
    ) -> None:
        """批量 upsert（事务包裹），用于 tick 写入

        entries 元素: (type_, key, value, updated_at, change_type, detail[, source[, value_text]])
        """
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")

            for entry in entries:
                source = entry[6] if len(entry) > 6 else ""
                value_text = entry[7] if len(entry) > 7 else ""
                type_, key, value, updated_at, change_type, detail = entry[:6]
                write_ts = datetime.now().isoformat(timespec="seconds")
                ts = updated_at or write_ts

                old_row = conn.execute(
                    "SELECT value, value_text FROM profile_entries "
                    "WHERE username=? AND type=? AND key=?",
                    (username, type_, key),
                ).fetchone()
                old_value = old_row[0] if old_row else None
                old_value_text = old_row[1] if old_row and len(old_row) > 1 else ""

                conn.execute(
                    "INSERT OR REPLACE INTO profile_entries "
                    "(username, type, key, value, value_text, updated_at, updated_time) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (username, type_, key, float(value), value_text, ts, write_ts),
                )

                if change_type is not None:
                    should_record = False
                    if change_type in ("action", "override"):
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
                            "old_value_text, new_value_text, change_type, detail, source) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (write_ts, username, type_, key, old_value, float(value),
                             old_value_text or "", value_text,
                             change_type, detail, source),
                        )

            conn.commit()
        finally:
            conn.close()

    def update_if_current(
        self,
        username: str,
        type_: str,
        key: str,
        *,
        expected_value: float | int,
        expected_updated_at: str,
        new_value: float | int,
        new_updated_at: str | None = None,
        change_type: str | None = None,
        detail: str = "",
        source: str = "",
    ) -> bool:
        """CAS 更新单条 entry。

        仅当当前 value 与 updated_at 均仍等于调用方读到的快照时才写入。
        返回 True 表示更新成功；False 表示状态已被其他进程修改，本轮调用方应放弃。
        """
        old_value = float(expected_value)
        value = float(new_value)
        write_ts = datetime.now().isoformat(timespec="seconds")
        ts = new_updated_at or write_ts
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "UPDATE profile_entries "
                "SET value=?, updated_at=?, updated_time=? "
                "WHERE username=? AND type=? AND key=? "
                "AND value=? AND updated_at=?",
                (value, ts, write_ts, username, type_, key, old_value, expected_updated_at),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                return False

            if change_type is not None:
                should_record = False
                if change_type in ("action", "override"):
                    should_record = True
                elif old_value != value:
                    should_record = True

                if should_record:
                    conn.execute(
                        "INSERT INTO profile_history "
                        "(ts, username, type, key, old_value, new_value, "
                        "change_type, detail, source) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (write_ts, username, type_, key, old_value, value,
                         change_type, detail, source),
                    )

            conn.commit()
            return True
        finally:
            conn.close()

    # ─── History 查询 ───

    def get_history(
        self,
        username: str,
        type_: str | None = None,
        key: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """查询变更历史，支持按 type/key 过滤，按时间倒序"""
        conditions = ["username = ?"]
        params: list = [username]

        if type_ is not None:
            conditions.append("type = ?")
            params.append(type_)
        if key is not None:
            conditions.append("key = ?")
            params.append(key)

        where = " AND ".join(conditions)
        params.append(limit)

        conn = self._connect()
        try:
            rows = conn.execute(
                f"SELECT id, ts, username, type, key, old_value, new_value, "
                f"old_value_text, new_value_text, change_type, detail, source "
                f"FROM profile_history "
                f"WHERE {where} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        finally:
            conn.close()

        return [
            {
                "id": r[0], "ts": r[1], "username": r[2],
                "type": r[3], "key": r[4],
                "old_value": r[5], "new_value": r[6],
                "old_value_text": r[7] or "", "new_value_text": r[8] or "",
                "change_type": r[9], "detail": r[10], "source": r[11],
            }
            for r in rows
        ]

    def cleanup_history(self, days: int = 90) -> int:
        """清理 N 天前的历史记录，返回删除行数"""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")

        conn = self._connect()
        try:
            cursor = conn.execute(
                "DELETE FROM profile_history WHERE ts < ?", (cutoff,)
            )
            conn.commit()
            deleted = cursor.rowcount
        finally:
            conn.close()

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


def db_read_entry(username: str, type_: str, key: str) -> dict:
    return get_profile_db().get_entry(username, type_, key)


def db_read_all(username: str) -> dict[str, dict[str, dict]]:
    return get_profile_db().get_all(username)


def db_upsert(
    username: str,
    type_: str,
    key: str,
    value: float | int,
    updated_at: str | None = None,
    change_type: str | None = None,
    detail: str = "",
    source: str = "",
    value_text: str = "",
) -> None:
    get_profile_db().upsert(username, type_, key, value, updated_at, change_type, detail, source, value_text)


def db_upsert_many(username: str, entries: list[tuple]) -> None:
    get_profile_db().upsert_many(username, entries)


def db_update_if_current(
    username: str,
    type_: str,
    key: str,
    *,
    expected_value: float | int,
    expected_updated_at: str,
    new_value: float | int,
    new_updated_at: str | None = None,
    change_type: str | None = None,
    detail: str = "",
    source: str = "",
) -> bool:
    return get_profile_db().update_if_current(
        username,
        type_,
        key,
        expected_value=expected_value,
        expected_updated_at=expected_updated_at,
        new_value=new_value,
        new_updated_at=new_updated_at,
        change_type=change_type,
        detail=detail,
        source=source,
    )


def db_get_history(
    username: str,
    type_: str | None = None,
    key: str | None = None,
    limit: int = 100,
) -> list[dict]:
    return get_profile_db().get_history(username, type_, key, limit)


def db_cleanup_history(days: int = 90) -> int:
    return get_profile_db().cleanup_history(days)
