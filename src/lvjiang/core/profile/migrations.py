"""``profile.db`` 的 schema 版本与逐版迁移。

这个模块是**历史快照**：迁移一旦发布就不得再按今天的业务语义改写，否则同一份
旧库在不同版本上升级会得到不同结果。版本推进、写锁与升级前备份由
``core.db.Schema`` 统一执行，这里只提供各版本自己的 SQL。

新增版本时：追加一个 ``_migrate_vNN`` 函数并把 ``(版本号, 描述, 函数)`` 加到
``MIGRATIONS`` 末尾，版本号从 1 连续递增。
"""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from .history_migration import migrate_structured_history

# ─── Schema 版本管理 ──────────────────────────────────────────


def _migrate_v1(conn: sqlite3.Connection) -> None:
    """初始建表: profile_entries + profile_history"""
    # 不使用 executescript：它会先隐式提交已有事务，破坏
    # Schema.ensure 持有的 BEGIN IMMEDIATE 迁移锁。
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


def _migrate_v10(conn: sqlite3.Connection) -> None:
    """history 升级为「跨端可还原的账本」：补行身份与来源作者。

    行身份是 ``(device_id, origin_local_id)``——「哪台设备上的第几号操作」，
    而不是本地自增 ``id``。本地自增 id 会在库重建后重新编号，拿它判重必然
    在重建窗口期失效；``origin_local_id`` 由写入方给出，重建后仍然一致。

    - 本机写入：插入后回填成自己的 id（见 ``EntryStore.upsert``）。
    - 跨端回放：写来源实例 id 与该实例的 ``local_id``。

    唯一索引让回放幂等（页级重试、水位没落盘就崩溃都不会写第二行）。
    旧行全部是本机写的，直接回填成自身 id；``device_id`` 为空的存量行出处
    不明（不上传），保持 NULL 不参与唯一约束。
    """
    cols = [row[1] for row in conn.execute("PRAGMA table_info(profile_history)").fetchall()]
    if "origin_local_id" not in cols:
        conn.execute(
            "ALTER TABLE profile_history ADD COLUMN origin_local_id INTEGER")
    conn.execute(
        "UPDATE profile_history SET origin_local_id = id "
        "WHERE origin_local_id IS NULL AND device_id <> ''")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_history_origin "
        "ON profile_history(device_id, origin_local_id)")


def _realtime_rate_table() -> dict[str, tuple[float, str, float | None]]:
    """从 profile.yaml 读取 realtime regen 的速率。迁移时只读，打不开就跳过还原。"""
    import yaml

    from lvjiang.constants import SESSION_CONFIG_DIR

    path = SESSION_CONFIG_DIR / "profile.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        return {}
    if not isinstance(data, dict):
        return {}
    rates: dict[str, tuple[float, str, float | None]] = {}
    for item in data.get("regen") or []:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if item.get("regen_type") != "realtime" or not isinstance(key, str):
            continue
        try:
            rate = float(item.get("regen_rate_value") or 0)
        except (TypeError, ValueError):
            continue
        unit = item.get("regen_rate_unit") or "hour"
        cap = item.get("cap")
        cap_value = float(cap) if isinstance(cap, (int, float)) else None
        if rate > 0:
            rates[key] = (rate, str(unit), cap_value)
    return rates


def _rewrite_entries_v11(conn: sqlite3.Connection) -> None:
    """用账本里最新的 set/delta 重写条目锚点。realtime 在改写前先还原小数。"""
    from .arbitrate import canonical_event, event_rank, parse_event
    from .regen import period_seconds

    rates = _realtime_rate_table()
    entries = conn.execute(
        "SELECT username, type, key, value, updated_at FROM profile_entries"
    ).fetchall()
    history = conn.execute(
        "SELECT username, type, key, new_value, ts, op_id FROM profile_history "
        "WHERE change_type IN ('set', 'delta')"
    ).fetchall()
    best: dict[tuple[str, str, str], tuple] = {}
    for username, type_, key, new_value, ts, op_id in history:
        ident = (username, type_, key)
        rank = event_rank(ts, op_id)
        current = best.get(ident)
        if current is None or rank > current[0]:
            best[ident] = (rank, new_value, ts, op_id)
    for username, type_, key, value, updated_at in entries:
        winner = best.get((username, type_, key))
        if winner is None:
            continue
        _rank, new_value, ts, op_id = winner
        stored = float(value or 0)
        written = float(new_value or 0)
        event_at = canonical_event(ts) or canonical_event(updated_at) or (ts or "")
        if type_ == "regen" and key in rates and written == stored:
            rate, unit, cap = rates[key]
            anchor = parse_event(updated_at)
            event = parse_event(ts)
            exact = stored
            if anchor is not None and event is not None:
                elapsed = max((event - anchor).total_seconds(), 0.0)
                exact = stored + elapsed / period_seconds(unit) * rate
                if cap is not None:
                    exact = min(exact, float(cap))
            written = exact
        conn.execute(
            "UPDATE profile_entries SET value=?, updated_at=?, op_id=? "
            "WHERE username=? AND type=? AND key=?",
            (written, event_at, op_id, username, type_, key),
        )


def _migrate_v11(conn: sqlite3.Connection) -> None:
    """set-only：加 op_id / remote_id，按来源身份回填，再重写条目锚点。"""
    history_cols = [
        row[1] for row in conn.execute("PRAGMA table_info(profile_history)").fetchall()
    ]
    if "op_id" not in history_cols:
        conn.execute(
            "ALTER TABLE profile_history ADD COLUMN op_id TEXT NOT NULL DEFAULT ''")
    if "remote_id" not in history_cols:
        conn.execute("ALTER TABLE profile_history ADD COLUMN remote_id INTEGER")
    entry_cols = [
        row[1] for row in conn.execute("PRAGMA table_info(profile_entries)").fetchall()
    ]
    if "op_id" not in entry_cols:
        conn.execute(
            "ALTER TABLE profile_entries ADD COLUMN op_id TEXT NOT NULL DEFAULT ''")
    conn.execute(
        "UPDATE profile_history "
        "SET op_id = 'legacy:' || device_id || ':' || origin_local_id "
        "WHERE op_id = '' AND device_id <> '' AND origin_local_id IS NOT NULL")
    conn.execute(
        "UPDATE profile_history SET op_id = 'unscoped:' || id WHERE op_id = ''")
    conn.execute("DROP INDEX IF EXISTS idx_history_origin")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_history_op_id ON profile_history(op_id)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_history_pending "
        "ON profile_history(device_id, id) "
        "WHERE remote_id IS NULL AND change_type IN ('set', 'delta')")
    _rewrite_entries_v11(conn)


def _migrate_v12(conn: sqlite3.Connection) -> None:
    """统一账本字段命名（原地 ALTER，不搬数据），与云端保持同构。

    云端已用同一套列名，本地因此成为云端的子集（只少 account_id）。
    时间语义：``event_at`` 为业务事件时刻，``created_at`` 为真实写入时刻。
    """

    def columns(table: str) -> set[str]:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}

    entry_cols = columns("profile_entries")
    if "updated_at" in entry_cols and "event_at" not in entry_cols:
        conn.execute("ALTER TABLE profile_entries RENAME COLUMN updated_at TO event_at")
    if "updated_time" in entry_cols and "created_at" not in entry_cols:
        conn.execute("ALTER TABLE profile_entries RENAME COLUMN updated_time TO created_at")

    history_cols = columns("profile_history")
    for old, new in (("ts", "event_at"), ("change_type", "op_type"),
                     ("device_id", "instance_id"), ("id", "local_id"),
                     ("new_value_text", "value_text")):
        if old in history_cols and new not in history_cols:
            conn.execute(f"ALTER TABLE profile_history RENAME COLUMN {old} TO {new}")
            history_cols.discard(old)
            history_cols.add(new)
    if "created_at" not in history_cols:
        conn.execute("ALTER TABLE profile_history ADD COLUMN created_at TEXT NOT NULL DEFAULT ''")
        # 旧行不知道真实写入时刻，用事件时刻回填（唯一可用的近似值）。
        conn.execute("UPDATE profile_history SET created_at = event_at")
    # 已被 local_id 取代的簿记列：直接丢弃（不是业务数据）。
    if "origin_local_id" in history_cols:
        conn.execute("ALTER TABLE profile_history DROP COLUMN origin_local_id")


def _migrate_v13(conn: sqlite3.Connection) -> None:
    """系统写入类型正名：tick → regen（存量不回填由水位门保证）。

    tick 是时间派生中间物的旧名；概念强化后系统行分 reset（重置）/regen（恢复），
    source 语义只留给人的写入。历史行**只改类型名**，source 与数值一律不动。
    存量行不上传由上传侧的快照水位门保证（local_id > 水位才是候选），
    不需要任何标记。
    """
    conn.execute("UPDATE profile_history SET op_type='regen' WHERE op_type='tick'")


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
    (10, "history gains origin row identity", _migrate_v10),
    (11, "profile set-only in-place", _migrate_v11),
    (12, "unify profile ledger columns with cloud", _migrate_v12),
    (13, "rename tick op_type to regen", _migrate_v13),
]

CURRENT_VERSION = MIGRATIONS[-1][0]


def backup_before_upgrade(conn: sqlite3.Connection, path: Path) -> None:
    """升级前留一份可用 SQLite 打开的备份，并另存一份带时间戳的副本。

    备份文件按目标版本命名：这次加列之后如果忘了改版本号，下次升级不会因为
    「文件已存在」而跳过备份。
    """
    backup_path = path.with_suffix(f".before-v{CURRENT_VERSION}.db")
    if backup_path.exists():
        return
    # sqlite3.connect 的上下文管理器只提交事务、**不关连接**，必须显式 close：
    # 这里每次首次升级都会执行，不关就一条条泄漏下去。
    backup = sqlite3.connect(backup_path)
    try:
        conn.backup(backup)
    finally:
        backup.close()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = path.parent / f"_migration-backup-{stamp}"
    folder.mkdir(exist_ok=True)
    shutil.copy2(backup_path, folder / backup_path.name)
    account = path.parent / "account.json"
    if account.is_file():
        shutil.copy2(account, folder / "account.json")
