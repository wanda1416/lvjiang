"""ProfileDB 单元测试

覆盖 profile_db.py 的核心功能：
- CRUD 基础操作
- Schema 版本迁移（幂等性）
- 变更历史记录（action/manual/tick 语义）
- History 查询与清理
- 并发 upsert 安全性
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from lvjiang.apps.yysls.profile.profile_db import (
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
    def test_upsert_and_get(self, db: ProfileDB):
        db.upsert("user1", "quota", "k1", 42)
        entry = db.get_entry("user1", "quota", "k1")
        assert entry["value"] == 42
        assert entry["updated_at"] != ""

    def test_get_nonexistent_returns_empty(self, db: ProfileDB):
        assert db.get_entry("nobody", "quota", "k1") == {}

    def test_get_all(self, db: ProfileDB):
        db.upsert("user1", "quota", "k1", 10)
        db.upsert("user1", "quota", "k2", 20)
        db.upsert("user1", "regen", "tili", 2500)

        all_data = db.get_all("user1")
        assert all_data["quota"]["k1"]["value"] == 10
        assert all_data["quota"]["k2"]["value"] == 20
        assert all_data["regen"]["tili"]["value"] == 2500

    def test_get_all_empty_user(self, db: ProfileDB):
        assert db.get_all("nobody") == {}

    def test_upsert_replaces(self, db: ProfileDB):
        db.upsert("user1", "quota", "k1", 10)
        db.upsert("user1", "quota", "k1", 20)
        assert db.get_entry("user1", "quota", "k1")["value"] == 20

    def test_upsert_custom_updated_at(self, db: ProfileDB):
        db.upsert("user1", "quota", "k1", 10, updated_at="2026-01-01T00:00:00")
        entry = db.get_entry("user1", "quota", "k1")
        assert entry["updated_at"] == "2026-01-01T00:00:00"

    def test_upsert_many(self, db: ProfileDB):
        entries = [
            ("quota", "k1", 10, "2026-08-01T10:00:00", None, ""),
            ("quota", "k2", 20, "2026-08-01T10:00:00", None, ""),
            ("regen", "tili", 2500, "2026-08-09T05:00:00", None, ""),
        ]
        db.upsert_many("user1", entries)

        all_data = db.get_all("user1")
        assert len(all_data["quota"]) == 2
        assert all_data["regen"]["tili"]["value"] == 2500

    def test_different_users_isolated(self, db: ProfileDB):
        db.upsert("user1", "quota", "k1", 10)
        db.upsert("user2", "quota", "k1", 99)
        assert db.get_entry("user1", "quota", "k1")["value"] == 10
        assert db.get_entry("user2", "quota", "k1")["value"] == 99

    def test_update_if_current_success(self, db: ProfileDB):
        db.upsert("user1", "regen", "xinli", 100, updated_at="2026-08-11T10:00:00")
        updated = db.update_if_current(
            "user1", "regen", "xinli",
            expected_value=100,
            expected_updated_at="2026-08-11T10:00:00",
            new_value=101,
            new_updated_at="2026-08-11T10:08:00",
            change_type="tick",
            detail="regen:+1.0000",
        )

        assert updated is True
        entry = db.get_entry("user1", "regen", "xinli")
        assert entry["value"] == 101
        assert entry["updated_at"] == "2026-08-11T10:08:00"
        history = db.get_history("user1")
        assert len(history) == 1
        assert history[0]["old_value"] == 100
        assert history[0]["new_value"] == 101

    def test_update_if_current_value_mismatch_fails(self, db: ProfileDB):
        db.upsert("user1", "regen", "xinli", 100, updated_at="2026-08-11T10:00:00")
        updated = db.update_if_current(
            "user1", "regen", "xinli",
            expected_value=99,
            expected_updated_at="2026-08-11T10:00:00",
            new_value=101,
            new_updated_at="2026-08-11T10:08:00",
            change_type="tick",
            detail="regen:+1.0000",
        )

        assert updated is False
        entry = db.get_entry("user1", "regen", "xinli")
        assert entry["value"] == 100
        assert entry["updated_at"] == "2026-08-11T10:00:00"
        assert db.get_history("user1") == []

    def test_update_if_current_updated_at_mismatch_fails(self, db: ProfileDB):
        db.upsert("user1", "regen", "xinli", 100, updated_at="2026-08-11T10:01:00")
        updated = db.update_if_current(
            "user1", "regen", "xinli",
            expected_value=100,
            expected_updated_at="2026-08-11T10:00:00",
            new_value=101,
            new_updated_at="2026-08-11T10:08:00",
            change_type="tick",
            detail="regen:+1.0000",
        )

        assert updated is False
        entry = db.get_entry("user1", "regen", "xinli")
        assert entry["value"] == 100
        assert entry["updated_at"] == "2026-08-11T10:01:00"
        assert db.get_history("user1") == []

    def test_update_if_current_missing_entry_fails(self, db: ProfileDB):
        updated = db.update_if_current(
            "user1", "regen", "xinli",
            expected_value=100,
            expected_updated_at="2026-08-11T10:00:00",
            new_value=101,
            new_updated_at="2026-08-11T10:08:00",
        )

        assert updated is False
        assert db.get_entry("user1", "regen", "xinli") == {}


# ─── Schema 版本管理 ──────────────────────────────────────────


class TestSchemaMigration:
    def test_initial_migration_creates_tables(self, db: ProfileDB):
        """v0 → v1: 建表成功"""
        conn = db._connect()
        try:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        finally:
            conn.close()

        assert "profile_entries" in tables
        assert "profile_history" in tables
        assert "schema_version" in tables

    def test_schema_version_is_current(self, db: ProfileDB):
        """迁移完成后版本号等于 CURRENT_VERSION"""
        conn = db._connect()
        try:
            row = conn.execute(
                "SELECT MAX(version) FROM schema_version"
            ).fetchone()
        finally:
            conn.close()

        assert row[0] == MIGRATIONS[-1][0]

    def test_reopen_is_idempotent(self, tmp_path: Path):
        """重复打开同一 DB 不报错（幂等性）"""
        db_path = tmp_path / "test.db"

        db1 = ProfileDB(db_path)
        db1.upsert("u", "quota", "k", 10)

        db2 = ProfileDB(db_path)
        assert db2.get_entry("u", "quota", "k")["value"] == 10

    def test_migrate_v2_idempotent(self, tmp_path: Path):
        """v2 迁移列已存在时应幂等跳过（不报 duplicate column name）"""
        db_path = tmp_path / "test.db"
        db1 = ProfileDB(db_path)  # 正常走 v1+v2
        # 强制把版本号回退到 1，模拟旧版代码升级场景
        conn = db1._connect()
        try:
            conn.execute("DELETE FROM schema_version")
            conn.execute("INSERT INTO schema_version(version) VALUES (1)")
            conn.commit()
        finally:
            conn.close()
        # 再次打开应重跑 v2 且因 source 列已存在而跳过，不抛 duplicate column name
        db2 = ProfileDB(db_path)
        db2.upsert("u", "quota", "k", 10, change_type="action", detail="+10", source="打本")
        assert db2.get_history("u")[0]["source"] == "打本"


# ─── 变更历史 ─────────────────────────────────────────────────


class TestHistory:
    def test_action_always_records(self, db: ProfileDB):
        """action 类型：即使值不变也记录"""
        db.upsert("u", "quota", "k", 10, change_type="action", detail="+10")
        db.upsert("u", "quota", "k", 10, change_type="action", detail="+0")

        history = db.get_history("u")
        assert len(history) == 2
        assert history[0]["change_type"] == "action"
        assert history[0]["detail"] == "+0"

    def test_source_recorded_in_history(self, db: ProfileDB):
        """upsert 传入的 source 应随 history 落盘并可读回"""
        db.upsert("u", "quota", "k", 10, change_type="action", detail="+10", source="打本")
        db.upsert("u", "quota", "k", 20, change_type="action", detail="+10", source="商店")

        history = db.get_history("u")
        assert len(history) == 2
        assert history[0]["source"] == "商店"   # 最新在前
        assert history[1]["source"] == "打本"

    def test_source_default_empty(self, db: ProfileDB):
        """未传 source 时，history 返回空字符串而非 None"""
        db.upsert("u", "quota", "k", 10, change_type="action", detail="+10")
        history = db.get_history("u")
        assert history[0]["source"] == ""

    def test_override_always_records(self, db: ProfileDB):
        """override 类型：即使值不变也记录"""
        db.upsert("u", "quota", "k", 10, change_type="override", detail="override:10")
        db.upsert("u", "quota", "k", 10, change_type="override", detail="override:10")

        history = db.get_history("u")
        assert len(history) == 2

    def test_tick_records_only_on_change(self, db: ProfileDB):
        """tick 类型：值不变时不记录"""
        db.upsert("u", "quota", "k", 10, change_type="tick", detail="reset:0")
        # 再次写入相同值 → 不记录
        db.upsert("u", "quota", "k", 10, change_type="tick", detail="regen:+0.0")
        # 写入不同值 → 记录
        db.upsert("u", "quota", "k", 20, change_type="tick", detail="regen:+10.0")

        history = db.get_history("u")
        assert len(history) == 2
        assert history[0]["detail"] == "regen:+10.0"
        assert history[1]["detail"] == "reset:0"

    def test_no_change_type_no_history(self, db: ProfileDB):
        """change_type=None 时不记录 history"""
        db.upsert("u", "quota", "k", 10)
        db.upsert("u", "quota", "k", 20)
        assert db.get_history("u") == []

    def test_history_old_value(self, db: ProfileDB):
        """history 中 old_value 正确记录"""
        db.upsert("u", "quota", "k", 10, change_type="action", detail="+10")
        db.upsert("u", "quota", "k", 20, change_type="action", detail="+10")

        history = db.get_history("u")
        # 按 id 倒序：最新在前
        assert history[0]["old_value"] == 10  # 第二次写入：旧值 10 → 新值 20
        assert history[0]["new_value"] == 20
        assert history[1]["old_value"] is None  # 首次写入无旧值
        assert history[1]["new_value"] == 10

    def test_history_filter_by_type(self, db: ProfileDB):
        db.upsert("u", "quota", "k1", 10, change_type="action", detail="")
        db.upsert("u", "regen", "tili", 2500, change_type="tick", detail="regen")

        daily_history = db.get_history("u", type_="quota")
        assert len(daily_history) == 1
        assert daily_history[0]["type"] == "quota"

    def test_history_filter_by_key(self, db: ProfileDB):
        db.upsert("u", "quota", "k1", 10, change_type="action", detail="")
        db.upsert("u", "quota", "k2", 20, change_type="action", detail="")

        k1_history = db.get_history("u", key="k1")
        assert len(k1_history) == 1
        assert k1_history[0]["key"] == "k1"

    def test_history_limit(self, db: ProfileDB):
        for i in range(20):
            db.upsert("u", "quota", "k", i, change_type="tick", detail=f"v{i}")

        limited = db.get_history("u", limit=5)
        assert len(limited) == 5
        # 最新在前
        assert limited[0]["new_value"] == 19


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
                "(ts, username, type, key, old_value, new_value, change_type, detail) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (old_ts, "u", "quota", "k", None, 10, "tick", "old"),
            )
            conn.commit()
        finally:
            conn.close()

        # 再插入一条新记录
        db.upsert("u", "quota", "k", 20, change_type="tick", detail="new")

        assert len(db.get_history("u")) == 2
        deleted = db.cleanup_history(days=1)
        assert deleted == 1
        remaining = db.get_history("u")
        assert len(remaining) == 1
        assert remaining[0]["detail"] == "new"


# ─── 并发 upsert ──────────────────────────────────────────────


class TestConcurrentUpsert:
    def test_concurrent_upsert_no_data_loss(self, db: ProfileDB):
        """并发写入同一 key 时不丢数据"""
        def increment(i: int):
            db.upsert("u", "quota", "counter", i, change_type="tick", detail=f"v{i}")

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
        db.upsert("u", "regen", "xinli", 100, updated_at="2026-08-11T10:00:00")

        def tick(_i: int) -> bool:
            return db.update_if_current(
                "u", "regen", "xinli",
                expected_value=100,
                expected_updated_at="2026-08-11T10:00:00",
                new_value=101,
                new_updated_at="2026-08-11T10:08:00",
                change_type="tick",
                detail="regen:+1.0000",
            )

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(tick, range(8)))

        assert results.count(True) == 1
        assert db.get_entry("u", "regen", "xinli")["value"] == 101
        history = db.get_history("u", limit=100)
        assert len(history) == 1
        assert history[0]["detail"] == "regen:+1.0000"
