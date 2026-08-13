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
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger

from lvjiang.constants import SESSION_CONFIG_DIR

# 数据库路径
_DB_PATH = SESSION_CONFIG_DIR / "profile.db"


# ─── Schema 版本管理 ──────────────────────────────────────────


def _migrate_v1(conn: sqlite3.Connection) -> None:
    """初始建表: profile_entries + profile_history"""
    conn.executescript("""
        CREATE TABLE profile_entries (
            username   TEXT NOT NULL,
            type       TEXT NOT NULL,
            key        TEXT NOT NULL,
            value      REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (username, type, key)
        );
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
        );
        CREATE INDEX idx_history_user_key
            ON profile_history(username, type, key, id DESC);
    """)


# 有序迁移列表: (版本号, 描述, 迁移函数)
MIGRATIONS: list[tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (1, "initial schema", _migrate_v1),
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
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    # ─── Schema 迁移 ───

    def _ensure_schema(self) -> None:
        """检测当前 schema 版本，依次执行未应用的迁移"""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
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
        finally:
            conn.close()

    # ─── 读取 ───

    def get_entry(self, username: str, type_: str, key: str) -> dict:
        """读取单条 entry，不存在返回空 dict"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT value, updated_at FROM profile_entries "
                "WHERE username=? AND type=? AND key=?",
                (username, type_, key),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return {}
        return {"value": row[0], "updated_at": row[1]}

    def get_all(self, username: str) -> dict[str, dict[str, dict]]:
        """读取用户全部 profile

        Returns: {type: {key: {value, updated_at}}}
        格式与 user.json 的 profile 节点兼容。
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT type, key, value, updated_at FROM profile_entries "
                "WHERE username=?",
                (username,),
            ).fetchall()
        finally:
            conn.close()

        result: dict[str, dict[str, dict]] = {}
        for type_, key, value, updated_at in rows:
            model_data = result.setdefault(type_, {})
            model_data[key] = {"value": value, "updated_at": updated_at}
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
    ) -> None:
        """INSERT OR REPLACE 单条 entry

        change_type 非 None 时记录 history（内部对比 old/new 值，无变化跳过）。
        锁冲突抛 sqlite3.OperationalError。
        """
        ts = updated_at or datetime.now().isoformat(timespec="seconds")
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")

            # 读取旧值（用于 history 对比）
            old_row = conn.execute(
                "SELECT value FROM profile_entries WHERE username=? AND type=? AND key=?",
                (username, type_, key),
            ).fetchone()
            old_value = old_row[0] if old_row else None

            # upsert entry
            conn.execute(
                "INSERT OR REPLACE INTO profile_entries "
                "(username, type, key, value, updated_at) VALUES (?, ?, ?, ?, ?)",
                (username, type_, key, float(value), ts),
            )

            # history 记录
            if change_type is not None:
                should_record = False
                if change_type in ("action", "override"):
                    # 用户主动操作始终记录
                    should_record = True
                elif old_value != float(value):
                    # tick 仅在值变化时记录
                    should_record = True

                if should_record:
                    now_ts = datetime.now().isoformat(timespec="seconds")
                    conn.execute(
                        "INSERT INTO profile_history "
                        "(ts, username, type, key, old_value, new_value, change_type, detail) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (now_ts, username, type_, key, old_value, float(value),
                         change_type, detail),
                    )

            conn.commit()
        finally:
            conn.close()

    def upsert_many(
        self, username: str, entries: list[tuple]
    ) -> None:
        """批量 upsert（事务包裹），用于 tick 写入

        entries 元素: (type_, key, value, updated_at, change_type, detail)
        """
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")

            for type_, key, value, updated_at, change_type, detail in entries:
                ts = updated_at or datetime.now().isoformat(timespec="seconds")

                old_row = conn.execute(
                    "SELECT value FROM profile_entries "
                    "WHERE username=? AND type=? AND key=?",
                    (username, type_, key),
                ).fetchone()
                old_value = old_row[0] if old_row else None

                conn.execute(
                    "INSERT OR REPLACE INTO profile_entries "
                    "(username, type, key, value, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (username, type_, key, float(value), ts),
                )

                if change_type is not None:
                    should_record = False
                    if change_type in ("action", "override"):
                        should_record = True
                    elif old_value != float(value):
                        should_record = True

                    if should_record:
                        now_ts = datetime.now().isoformat(timespec="seconds")
                        conn.execute(
                            "INSERT INTO profile_history "
                            "(ts, username, type, key, old_value, new_value, "
                            "change_type, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (now_ts, username, type_, key, old_value, float(value),
                             change_type, detail),
                        )

            conn.commit()
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
                f"change_type, detail FROM profile_history "
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
                "change_type": r[7], "detail": r[8],
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


def get_profile_db() -> ProfileDB:
    """懒加载 ProfileDB 单例"""
    global _db
    if _db is None:
        _db = ProfileDB(_DB_PATH)
    return _db


def reset_profile_db() -> None:
    """重置单例（测试用）"""
    global _db
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
) -> None:
    get_profile_db().upsert(username, type_, key, value, updated_at, change_type, detail)


def db_upsert_many(username: str, entries: list[tuple]) -> None:
    get_profile_db().upsert_many(username, entries)


def db_get_history(
    username: str,
    type_: str | None = None,
    key: str | None = None,
    limit: int = 100,
) -> list[dict]:
    return get_profile_db().get_history(username, type_, key, limit)


def db_cleanup_history(days: int = 90) -> int:
    return get_profile_db().cleanup_history(days)
