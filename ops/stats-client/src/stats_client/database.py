"""本地 SQLite 缓存：建表 + 极简迁移。

表名统一加 ``remote_`` 前缀（``remote_daily``/``remote_roll_batch``/
``remote_install_day_rolls``），标出"这是远端镜像，不是本地产生的数据"，
和本地才有的 ``sync_*``/``metric_*``/``report_snapshot`` 区分开。

刻意不镜像 D1 的 ``installs`` 表——``remote_daily`` 已经冗余存了
``app_version``/``run_env``/``os_name`` 等维度列（源头 schema.sql 的注释：
"冗余：留存查询免 JOIN installs"），本地做维度分布/留存不需要 installs。
唯一需要的"当前累计安装数"存进 ``remote_scalar``，每次同步用一条
``SELECT COUNT(*) FROM installs`` 刷新，不落地任何 install 级别的行。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS remote_daily (
  day         TEXT NOT NULL,
  install_id  TEXT NOT NULL,
  first_day   TEXT NOT NULL,
  app_version TEXT NOT NULL,
  run_env     TEXT NOT NULL,
  os_name     TEXT NOT NULL,
  os_release  TEXT NOT NULL,
  arch        TEXT NOT NULL,
  ui_lang     TEXT NOT NULL,
  plugin      TEXT NOT NULL,
  PRIMARY KEY (day, install_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS remote_roll_batch (
  batch_id    TEXT PRIMARY KEY,
  install_id  TEXT NOT NULL,
  day         TEXT NOT NULL,
  app_version TEXT NOT NULL,
  plugin      TEXT NOT NULL,
  n_events    INTEGER NOT NULL,
  payload     TEXT NOT NULL,
  received_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_remote_roll_batch_received
  ON remote_roll_batch (received_at, batch_id);
CREATE INDEX IF NOT EXISTS idx_remote_roll_batch_day ON remote_roll_batch (day);

CREATE TABLE IF NOT EXISTS remote_install_day_rolls (
  install_id TEXT NOT NULL,
  day        TEXT NOT NULL,
  n_events   INTEGER NOT NULL,
  PRIMARY KEY (install_id, day)
) WITHOUT ROWID;

-- 远端标量指标（目前只有 installs_count），不含任何 install 级别的行
CREATE TABLE IF NOT EXISTS remote_scalar (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- 预计算的每日指标，避免每次开页面都在 remote_daily/remote_roll_batch 全表重算
CREATE TABLE IF NOT EXISTS metric_daily (
  day          TEXT PRIMARY KEY,
  dau          INTEGER NOT NULL,
  new_installs INTEGER NOT NULL,
  roll_sessions INTEGER NOT NULL,
  roll_rounds  INTEGER NOT NULL,
  computed_at  TEXT NOT NULL
);

-- 每张远端表的增量游标。daily/install_day_rolls 用 cursor_a 记"已完整同步到
-- 的 day"；roll_batch 用 (cursor_a, cursor_b) = (received_at, batch_id) 复合游标。
CREATE TABLE IF NOT EXISTS sync_cursor (
  table_name TEXT PRIMARY KEY,
  cursor_a   TEXT,
  cursor_b   TEXT
);

CREATE TABLE IF NOT EXISTS sync_runs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  ok          INTEGER NOT NULL DEFAULT 0,
  detail_json TEXT NOT NULL,
  duration_ms INTEGER,
  error       TEXT
);

CREATE TABLE IF NOT EXISTS report_snapshot (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at  TEXT NOT NULL,
  params_json TEXT NOT NULL,
  n_events    INTEGER,
  n_rolls     INTEGER,
  report_md   TEXT NOT NULL
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None)  # 显式 BEGIN/COMMIT，见下
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("PRAGMA journal_mode = WAL")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_SQL)
    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key = 'version'").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('version', ?)",
            (str(SCHEMA_VERSION),))
    # 目前只有一个版本，没有迁移要跑；SCHEMA_VERSION 往上加时在这里插 if 分支。
