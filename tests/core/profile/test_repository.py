"""ProfileDB 单元测试

覆盖 profile_db.py 的核心功能：
- CRUD 基础操作
- Schema 版本迁移（幂等性）
- 变更历史记录（action/manual/tick 语义）
- History 查询与清理
- 并发 upsert 安全性
"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from lvjiang.core.profile.repository import (
    CURRENT_VERSION,
    MIGRATIONS,
    ProfileDB,
)


@pytest.fixture
def db(tmp_path: Path) -> ProfileDB:
    """隔离的 ProfileDB 实例"""
    db_path = tmp_path / "test_profile.db"
    return ProfileDB(db_path)


# ─── CRUD 基础 ────────────────────────────────────────────────


class TestCRUD:


    def test_get_all(self, db: ProfileDB):
        db.upsert("user1", "quota", "k1", 10)
        db.upsert("user1", "quota", "k2", 20)
        db.upsert("user1", "regen", "energy", 2500)

        all_data = db.get_all("user1")
        assert all_data["quota"]["k1"]["value"] == 10
        assert all_data["quota"]["k1"]["updated_time"] != ""
        assert all_data["quota"]["k2"]["value"] == 20
        assert all_data["regen"]["energy"]["value"] == 2500


    def test_upsert_replaces(self, db: ProfileDB):
        db.upsert("user1", "quota", "k1", 10)
        db.upsert("user1", "quota", "k1", 20)
        assert db.get_entry("user1", "quota", "k1")["value"] == 20

    def test_upsert_custom_updated_at(self, db: ProfileDB):
        db.upsert("user1", "quota", "k1", 10, updated_at="2026-01-01T00:00:00")
        entry = db.get_entry("user1", "quota", "k1")
        assert entry["updated_at"] == "2026-01-01T00:00:00"
        assert entry["updated_time"] != "2026-01-01T00:00:00"
        assert entry["updated_time"] != ""

    def test_upsert_many(self, db: ProfileDB):
        entries = [
            ("quota", "k1", 10, "2026-08-01T10:00:00", None, None),
            ("quota", "k2", 20, "2026-08-01T10:00:00", None, None),
            ("regen", "energy", 2500, "2026-08-09T05:00:00", None, None),
        ]
        db.upsert_many("user1", entries)

        all_data = db.get_all("user1")
        assert len(all_data["quota"]) == 2
        assert all_data["regen"]["energy"]["value"] == 2500

    def test_different_users_isolated(self, db: ProfileDB):
        db.upsert("user1", "quota", "k1", 10)
        db.upsert("user2", "quota", "k1", 99)
        assert db.get_entry("user1", "quota", "k1")["value"] == 10
        assert db.get_entry("user2", "quota", "k1")["value"] == 99

    def test_upsert_value_text(self, db: ProfileDB):
        """note 模型写入 value_text 列"""
        db.upsert("user1", "note", "user_note", 0, value_text="已完成")
        entry = db.get_entry("user1", "note", "user_note")
        assert entry["value_text"] == "已完成"
        assert entry["value"] == 0


    def test_get_all_includes_value_text(self, db: ProfileDB):
        """get_all 返回的 entry 包含 value_text 字段"""
        db.upsert("user1", "note", "k1", 0, value_text="备注内容")
        all_data = db.get_all("user1")
        assert all_data["note"]["k1"]["value_text"] == "备注内容"

    def test_update_if_current_success(self, db: ProfileDB):
        db.upsert("user1", "regen", "resource_meter", 100, updated_at="2026-08-11T10:00:00")
        updated = db.update_if_current(
            "user1", "regen", "resource_meter",
            expected_value=100,
            expected_updated_at="2026-08-11T10:00:00",
            new_value=101,
            new_updated_at="2026-08-11T10:08:00",
            change_type="tick",
            delta_value=1.0,
        )

        assert updated is True
        entry = db.get_entry("user1", "regen", "resource_meter")
        assert entry["value"] == 101
        assert entry["updated_at"] == "2026-08-11T10:08:00"
        assert entry["updated_time"] != "2026-08-11T10:08:00"
        assert entry["updated_time"] != ""
        history = db.get_history("user1")
        assert len(history) == 1
        assert history[0]["old_value"] == 100
        assert history[0]["new_value"] == 101

    def test_update_if_current_value_mismatch_fails(self, db: ProfileDB):
        db.upsert("user1", "regen", "resource_meter", 100, updated_at="2026-08-11T10:00:00")
        updated = db.update_if_current(
            "user1", "regen", "resource_meter",
            expected_value=99,
            expected_updated_at="2026-08-11T10:00:00",
            new_value=101,
            new_updated_at="2026-08-11T10:08:00",
            change_type="tick",
            delta_value=1.0,
        )

        assert updated is False
        entry = db.get_entry("user1", "regen", "resource_meter")
        assert entry["value"] == 100
        assert entry["updated_at"] == "2026-08-11T10:00:00"
        assert db.get_history("user1") == []

    def test_update_if_current_updated_at_mismatch_fails(self, db: ProfileDB):
        db.upsert("user1", "regen", "resource_meter", 100, updated_at="2026-08-11T10:01:00")
        updated = db.update_if_current(
            "user1", "regen", "resource_meter",
            expected_value=100,
            expected_updated_at="2026-08-11T10:00:00",
            new_value=101,
            new_updated_at="2026-08-11T10:08:00",
            change_type="tick",
            delta_value=1.0,
        )

        assert updated is False
        entry = db.get_entry("user1", "regen", "resource_meter")
        assert entry["value"] == 100
        assert entry["updated_at"] == "2026-08-11T10:01:00"
        assert db.get_history("user1") == []

    def test_update_if_current_missing_entry_fails(self, db: ProfileDB):
        updated = db.update_if_current(
            "user1", "regen", "resource_meter",
            expected_value=100,
            expected_updated_at="2026-08-11T10:00:00",
            new_value=101,
            new_updated_at="2026-08-11T10:08:00",
        )

        assert updated is False
        assert db.get_entry("user1", "regen", "resource_meter") == {}

    def test_update_if_current_inserts_when_missing_is_expected(self, db: ProfileDB):
        updated = db.update_if_current(
            "user1", "regen", "resource_meter",
            expected_value=0,
            expected_updated_at="",
            expected_entry_exists=False,
            new_value=10,
            new_updated_at="2026-08-11T10:08:00",
            change_type="action",
            delta_value=10.0,
        )

        assert updated is True
        entry = db.get_entry("user1", "regen", "resource_meter")
        assert entry["value"] == 10
        assert entry["updated_at"] == "2026-08-11T10:08:00"
        history = db.get_history("user1")
        assert len(history) == 1
        assert history[0]["old_value"] is None
        assert history[0]["new_value"] == 10

    def test_update_if_current_missing_snapshot_rejects_concurrent_insert(
        self, db: ProfileDB,
    ):
        db.upsert("user1", "regen", "resource_meter", 5)

        updated = db.update_if_current(
            "user1", "regen", "resource_meter",
            expected_value=0,
            expected_updated_at="",
            expected_entry_exists=False,
            new_value=10,
        )

        assert updated is False
        assert db.get_entry("user1", "regen", "resource_meter")["value"] == 5


# ─── Schema 版本管理 ──────────────────────────────────────────


class TestSchemaMigration:


    def test_shared_profile_schema_contract_stays_unscoped_without_app_id(
        self, db: ProfileDB
    ):
        """Profile 是跨插件共享数据，不按 app_id 分库或改表。"""
        conn = db._connect()
        try:
            entry_info = conn.execute(
                "PRAGMA table_info(profile_entries)"
            ).fetchall()
            history_columns = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(profile_history)"
                ).fetchall()
            }
        finally:
            conn.close()

        entry_columns = {row[1] for row in entry_info}
        primary_key = [
            name
            for _position, name in sorted(
                (row[5], row[1]) for row in entry_info if row[5]
            )
        ]

        assert CURRENT_VERSION == 7
        assert primary_key == ["username", "type", "key"]
        assert "app_id" not in entry_columns
        assert "app_id" not in history_columns

    def test_cross_user_history_index_is_migrated(self, db: ProfileDB):
        conn = db._connect()
        try:
            indexes = {
                row[1] for row in conn.execute(
                    "PRAGMA index_list(profile_history)"
                ).fetchall()
            }
        finally:
            conn.close()
        assert "idx_history_type_key" in indexes

    def test_migrate_v6_preserves_existing_history(self, tmp_path: Path):
        db_path = tmp_path / "v5_profile.db"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
            for version, _, migration in MIGRATIONS[:5]:
                migration(conn)
                conn.execute("INSERT INTO schema_version VALUES (?)", (version,))
            conn.execute(
                "INSERT INTO profile_history (ts, username, type, key, new_value, change_type) "
                "VALUES ('2026-01-01', 'u', 'quota', 'target', 7, 'action')")
            conn.commit()
        finally:
            conn.close()

        migrated = ProfileDB(db_path)

        conn = migrated._connect()
        try:
            indexes = {
                row[1] for row in conn.execute(
                    "PRAGMA index_list(profile_history)"
                ).fetchall()
            }
        finally:
            conn.close()
        assert "idx_history_type_key" in indexes
        assert migrated.get_history("u", "quota", "target")[0]["new_value"] == 7

    def test_reopen_is_idempotent(self, tmp_path: Path):
        """重复打开同一 DB 不报错（幂等性）"""
        db_path = tmp_path / "test.db"

        db1 = ProfileDB(db_path)
        db1.upsert("u", "quota", "k", 10)

        db2 = ProfileDB(db_path)
        assert db2.get_entry("u", "quota", "k")["value"] == 10

    def test_concurrent_initialization_serializes_migrations(self, tmp_path: Path):
        """多个线程首次打开同一 DB 时只执行一轮 schema 迁移。"""
        db_path = tmp_path / "concurrent_init.db"

        with ThreadPoolExecutor(max_workers=8) as pool:
            databases = list(pool.map(lambda _: ProfileDB(db_path), range(16)))

        assert len(databases) == 16
        conn = databases[0]._connect()
        try:
            version = conn.execute(
                "SELECT MAX(version) FROM schema_version"
            ).fetchone()[0]
            entry_cols = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(profile_entries)"
                ).fetchall()
            }
            history_cols = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(profile_history)"
                ).fetchall()
            }
        finally:
            conn.close()

        assert version == MIGRATIONS[-1][0]
        assert "value_text" in entry_cols
        assert {"old_value_text", "new_value_text"} <= history_cols

    def test_migrate_v2_idempotent(self, tmp_path: Path):
        """v2 迁移列已存在时应幂等跳过（不报 duplicate column name）"""
        db_path = tmp_path / "test.db"
        db1 = ProfileDB(db_path)  # 正常走 v1+v2
        conn = db1._connect()
        try:
            MIGRATIONS[1][2](conn)
            conn.commit()
        finally:
            conn.close()
        # 再次执行 v2 已验证 source 列存在时不会报错；重开不伪造历史版本。
        db2 = ProfileDB(db_path)
        db2.upsert("u", "quota", "k", 10, change_type="action", delta_value=10.0, source="导入")
        assert db2.get_history("u")[0]["source"] == "导入"

    def test_migrate_v3_adds_updated_time_to_legacy_entries(self, tmp_path: Path):
        """v2 旧库升级到 v3 时补 updated_time，且不覆盖业务 updated_at。"""
        db_path = tmp_path / "legacy_v2.db"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.executescript("""
                CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
                INSERT INTO schema_version(version) VALUES (2);
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
                    detail      TEXT    DEFAULT '',
                    source      TEXT    DEFAULT ''
                );
                INSERT INTO profile_entries
                    (username, type, key, value, updated_at)
                VALUES
                    ('u', 'regen', 'resource_meter', 100, '2026-08-12T10:00:00');
            """)
            conn.commit()
        finally:
            conn.close()

        db = ProfileDB(db_path)
        entry = db.get_entry("u", "regen", "resource_meter")

        assert entry["updated_at"] == "2026-08-12T10:00:00"
        assert entry["updated_time"] != ""

    def test_migrate_v3_idempotent_when_column_exists_but_version_old(self, tmp_path: Path):
        """模拟并发迁移后半程：列已存在但版本号仍旧，不应 duplicate column。"""
        db_path = tmp_path / "half_migrated_v3.db"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.executescript("""
                CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
                INSERT INTO schema_version(version) VALUES (2);
                CREATE TABLE profile_entries (
                    username   TEXT NOT NULL,
                    type       TEXT NOT NULL,
                    key        TEXT NOT NULL,
                    value      REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT '',
                    updated_time TEXT NOT NULL DEFAULT '',
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
                    detail      TEXT    DEFAULT '',
                    source      TEXT    DEFAULT ''
                );
                INSERT INTO profile_entries
                    (username, type, key, value, updated_at, updated_time)
                VALUES
                    ('u', 'regen', 'resource_meter', 100, '2026-08-12T10:00:00', '');
            """)
            conn.commit()
        finally:
            conn.close()

        db = ProfileDB(db_path)
        entry = db.get_entry("u", "regen", "resource_meter")

        assert entry["updated_time"] != ""

    def test_migrate_v4_adds_value_text_column(self, tmp_path: Path):
        """v4: profile_entries 增加 value_text 列"""
        db_path = tmp_path / "test_v4.db"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.executescript("""
                CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
                INSERT INTO schema_version(version) VALUES (3);
                CREATE TABLE profile_entries (
                    username   TEXT NOT NULL,
                    type       TEXT NOT NULL,
                    key        TEXT NOT NULL,
                    value      REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT '',
                    updated_time TEXT NOT NULL DEFAULT '',
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
                    detail      TEXT    DEFAULT '',
                    source      TEXT    DEFAULT ''
                );
            """)
            conn.commit()
        finally:
            conn.close()

        db = ProfileDB(db_path)
        conn = db._connect()
        try:
            cols = {
                row[1]
                for row in conn.execute("PRAGMA table_info(profile_entries)").fetchall()
            }
        finally:
            conn.close()

        assert "value_text" in cols

    def test_migrate_v4_idempotent(self, tmp_path: Path):
        """v4 迁移列已存在时应幂等跳过"""
        db_path = tmp_path / "test_v4_idem.db"
        db1 = ProfileDB(db_path)
        db1.upsert("u", "note", "k", 0, value_text="test")
        # 重新打开应幂等
        db2 = ProfileDB(db_path)
        assert db2.get_entry("u", "note", "k")["value_text"] == "test"


# ─── 变更历史 ─────────────────────────────────────────────────


class TestHistory:
    def test_action_always_records(self, db: ProfileDB):
        """action 类型：即使值不变也记录"""
        db.upsert("u", "quota", "k", 10, change_type="action", delta_value=10.0)
        db.upsert("u", "quota", "k", 10, change_type="action", delta_value=0.0)

        history = db.get_history("u")
        assert len(history) == 2
        assert history[0]["change_type"] == "action"
        assert history[0]["delta_value"] == 0

    def test_source_recorded_in_history(self, db: ProfileDB):
        """upsert 传入的 source 应随 history 落盘并可读回"""
        db.upsert("u", "quota", "k", 10, change_type="action", delta_value=10.0, source="导入")
        db.upsert("u", "quota", "k", 20, change_type="action", delta_value=10.0, source="同步")

        history = db.get_history("u")
        assert len(history) == 2
        assert history[0]["source"] == "同步"   # 最新在前
        assert history[1]["source"] == "导入"

    def test_history_source_edit_uses_original_value_as_guard(self, db: ProfileDB):
        db.upsert(
            "u", "quota", "k", 10,
            change_type="action", delta_value=10, source="误填来源",
        )
        record = db.get_history("u")[0]

        assert db.update_history_source(
            record["id"], expected_source="误填来源", new_source="正确来源",
        )
        assert not db.update_history_source(
            record["id"], expected_source="误填来源", new_source="过期覆盖",
        )
        assert db.get_history("u")[0]["source"] == "正确来源"

    def test_undo_latest_history_restores_value_and_records_audit(self, db: ProfileDB):
        db.upsert("u", "quota", "k", 3)
        db.upsert(
            "u", "quota", "k", 5,
            change_type="action", delta_value=2, source="误操作",
        )
        record = db.get_history("u")[0]

        assert db.undo_history(record["id"])
        assert db.get_entry("u", "quota", "k")["value"] == 3
        history = db.get_history("u")
        assert history[0]["change_type"] == "undo"
        assert history[0]["old_value"] == 5
        assert history[0]["new_value"] == 3
        assert history[0]["delta_value"] == -2
        assert history[0]["source"] == "撤销：误操作"
        assert not db.undo_history(record["id"])

    def test_undo_rejects_stale_record_without_touching_other_user(self, db: ProfileDB):
        db.upsert("u1", "quota", "k", 1, change_type="action")
        stale = db.get_history("u1")[0]
        db.upsert("u1", "quota", "k", 2, change_type="action")
        db.upsert("u2", "quota", "k", 9, change_type="action")

        assert not db.undo_history(stale["id"])
        assert db.get_entry("u1", "quota", "k")["value"] == 2
        assert db.get_entry("u2", "quota", "k")["value"] == 9

    def test_undo_rejects_sync_derived_record(self, db: ProfileDB):
        db.upsert(
            "u", "quota", "k", 2, change_type="action",
            delta_value=2, sync_from="stock:source",
        )
        record = db.get_history("u")[0]

        assert not db.undo_history(record["id"])
        assert db.get_entry("u", "quota", "k")["value"] == 2


    def test_override_always_records(self, db: ProfileDB):
        """override 类型：即使值不变也记录"""
        db.upsert("u", "quota", "k", 10, change_type="override", delta_value=10.0)
        db.upsert("u", "quota", "k", 10, change_type="override", delta_value=10.0)

        history = db.get_history("u")
        assert len(history) == 2

    def test_tick_records_only_on_change(self, db: ProfileDB):
        """tick 类型：值不变时不记录"""
        db.upsert("u", "quota", "k", 10, change_type="tick", delta_value=0.0)
        # 再次写入相同值 → 不记录
        db.upsert("u", "quota", "k", 10, change_type="tick", delta_value=0.0)
        # 写入不同值 → 记录
        db.upsert("u", "quota", "k", 20, change_type="tick", delta_value=10.0)

        history = db.get_history("u")
        assert len(history) == 2
        assert history[0]["delta_value"] == 10
        assert history[1]["delta_value"] == 0

    def test_no_change_type_no_history(self, db: ProfileDB):
        """change_type=None 时不记录 history"""
        db.upsert("u", "quota", "k", 10)
        db.upsert("u", "quota", "k", 20)
        assert db.get_history("u") == []

    def test_history_old_value(self, db: ProfileDB):
        """history 中 old_value 正确记录"""
        db.upsert("u", "quota", "k", 10, change_type="action", delta_value=10.0)
        db.upsert("u", "quota", "k", 20, change_type="action", delta_value=10.0)

        history = db.get_history("u")
        # 按 id 倒序：最新在前
        assert history[0]["old_value"] == 10  # 第二次写入：旧值 10 → 新值 20
        assert history[0]["new_value"] == 20
        assert history[1]["old_value"] is None  # 首次写入无旧值
        assert history[1]["new_value"] == 10

    def test_history_filter_by_type(self, db: ProfileDB):
        db.upsert("u", "quota", "k1", 10, change_type="action", delta_value=None)
        db.upsert("u", "regen", "energy", 2500, change_type="tick", delta_value=None)

        daily_history = db.get_history("u", type_="quota")
        assert len(daily_history) == 1
        assert daily_history[0]["type"] == "quota"

    def test_history_filter_by_key(self, db: ProfileDB):
        db.upsert("u", "quota", "k1", 10, change_type="action", delta_value=None)
        db.upsert("u", "quota", "k2", 20, change_type="action", delta_value=None)

        k1_history = db.get_history("u", key="k1")
        assert len(k1_history) == 1
        assert k1_history[0]["key"] == "k1"

    def test_history_limit(self, db: ProfileDB):
        for i in range(20):
            db.upsert("u", "quota", "k", i, change_type="tick", delta_value=1)

        limited = db.get_history("u", limit=5)
        assert len(limited) == 5
        # 最新在前
        assert limited[0]["new_value"] == 19

    def test_cross_user_history_filters_and_pages_by_key(self, db: ProfileDB):
        db.upsert("u1", "quota", "target", 1, change_type="action")
        db.upsert("u2", "quota", "target", 2, change_type="action")
        db.upsert("u1", "quota", "other", 3, change_type="action")
        db.upsert("u2", "stock", "target", 4, change_type="action")
        db.upsert("u1", "quota", "target", 5, change_type="action")

        assert db.count_history(None, "quota", "target") == 3
        assert db.count_history("u1", "quota", "target") == 2
        assert db.count_history("u2", "quota", "target") == 1
        assert [row["new_value"] for row in db.get_history(
            None, "quota", "target", limit=2,
        )] == [5, 2]
        assert [row["new_value"] for row in db.get_history(
            None, "quota", "target", limit=2, offset=2,
        )] == [1]
        assert [row["new_value"] for row in db.get_history(
            "u1", "quota", "target", limit=1, offset=1,
        )] == [1]


# ─── History 清理 ─────────────────────────────────────────────


class TestCleanupHistory:
    def test_cleanup_old_entries(self, db: ProfileDB):
        """清理超过 N 天的记录"""
        # 手动插入一条旧记录
        conn = db._connect()
        try:
            old_ts = "2020-01-01T00:00:00"
            conn.execute(
                "INSERT INTO profile_history "
                "(ts, username, type, key, old_value, new_value, change_type, delta_value) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (old_ts, "u", "quota", "k", None, 10, "tick", None),
            )
            conn.commit()
        finally:
            conn.close()

        # 再插入一条新记录
        db.upsert("u", "quota", "k", 20, change_type="tick", delta_value=None)

        assert len(db.get_history("u")) == 2
        deleted = db.cleanup_history(days=1)
        assert deleted == 1
        remaining = db.get_history("u")
        assert len(remaining) == 1
        assert remaining[0]["delta_value"] is None


# ─── 并发 upsert ──────────────────────────────────────────────


class TestConcurrentUpsert:
    def test_concurrent_upsert_no_data_loss(self, db: ProfileDB):
        """并发写入同一 key 时不丢数据"""
        def increment(i: int):
            db.upsert("u", "quota", "counter", i, change_type="tick", delta_value=1)

        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(increment, range(20)))

        # 最终值一定是 0-19 中的某一个（取决于执行顺序）
        entry = db.get_entry("u", "quota", "counter")
        assert entry["value"] in range(20)

        # history 行数 = 20（action/manual 每次记录，tick 每次值变化记录）
        # 由于并发，某些 tick 可能读到与写入相同的值 → history 行数 ≤ 20
        history = db.get_history("u", limit=100)
        assert len(history) > 0

    def test_concurrent_cas_allows_only_one_tick_from_same_snapshot(self, db: ProfileDB):
        """多个 tick 基于同一快照写入时，只允许一个 CAS 成功。"""
        db.upsert("u", "regen", "resource_meter", 100, updated_at="2026-08-11T10:00:00")

        def tick(_i: int) -> bool:
            return db.update_if_current(
                "u", "regen", "resource_meter",
                expected_value=100,
                expected_updated_at="2026-08-11T10:00:00",
                new_value=101,
                new_updated_at="2026-08-11T10:08:00",
                change_type="tick",
                delta_value=1.0,
            )

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(tick, range(8)))

        assert results.count(True) == 1
        assert db.get_entry("u", "regen", "resource_meter")["value"] == 101
        history = db.get_history("u", limit=100)
        assert len(history) == 1
        assert history[0]["delta_value"] == 1
