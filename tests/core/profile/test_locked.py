"""profile key 锁定：用户级"这个 key 物理失效"的状态。

锁定后引擎完全忽略该值 —— 读取返回空、所有写入被拒绝、周期重置不再发生，
值永久停在锁定时的样子；总览里显示为深灰空白。锁定是"用户 × key"的状态，
存在 profile_entries.locked 列上（v8），不属于 profile.yaml 定义层。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from lvjiang.core.profile.repository import CURRENT_VERSION, ProfileDB


@pytest.fixture
def db(tmp_path: Path) -> ProfileDB:
    return ProfileDB(tmp_path / "locked.db")


@pytest.fixture
def profile_env(tmp_path, monkeypatch):
    """隔离的 profile 环境：schema 里有 quota/regen/note 三种 key"""
    import lvjiang.core.profile.repository as profile_db
    import lvjiang.core.profile.schema as profile_config

    session_dir = tmp_path / "session"
    session_dir.mkdir()

    monkeypatch.setattr(profile_config, "_config", None, raising=False)
    profile_path = session_dir / "profile.yaml"
    monkeypatch.setattr(profile_config, "_PROFILE_PATH", profile_path, raising=False)
    profile_path.write_text(
        yaml.dump(
            {
                "quota": [
                    {"key": "weekly_quota", "label": "周配额", "cap": 2,
                     "period": "week", "reset_time": "05:00", "reset_day": 1},
                ],
                "regen": [
                    {"key": "energy", "label": "体力", "cap": 100,
                     "regen_type": "boundary", "regen_amount": 10,
                     "regen_period": "day", "reset_time": "05:00",
                     "alert_red": 90},
                ],
                "note": [{"key": "user_note", "label": "状态备注"}],
                "stock": [{"key": "target_stock", "label": "同步目标"}],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(profile_db, "_db", None, raising=False)
    monkeypatch.setattr(
        profile_db, "_DB_PATH", session_dir / "profile.db", raising=False)

    yield SimpleNamespace(username="u1", session_dir=session_dir)


def _make_v7_db(path: Path) -> None:
    """构造 v7 形态的库：entries 没有 locked 列，schema_version 记为 7。"""
    with sqlite3.connect(path) as conn:
        conn.execute("""
            CREATE TABLE profile_entries (
                username TEXT NOT NULL, type TEXT NOT NULL, key TEXT NOT NULL,
                value REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT '',
                updated_time TEXT NOT NULL DEFAULT '',
                value_text TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (username, type, key)
            )
        """)
        conn.execute("""
            CREATE TABLE profile_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
                username TEXT NOT NULL, type TEXT NOT NULL, key TEXT NOT NULL,
                old_value REAL, new_value REAL NOT NULL,
                change_type TEXT NOT NULL, detail TEXT DEFAULT '',
                source TEXT DEFAULT '', delta_value REAL,
                old_value_text TEXT DEFAULT '', new_value_text TEXT DEFAULT '',
                sync_from TEXT DEFAULT ''
            )
        """)
        conn.execute(
            "CREATE INDEX idx_history_user_key "
            "ON profile_history(username, type, key, id DESC)")
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO schema_version VALUES (7)")
        conn.execute(
            "INSERT INTO profile_entries "
            "(username, type, key, value, updated_at, updated_time, value_text) "
            "VALUES ('u1', 'quota', 'k', 5, '2026-01-01T00:00:00', "
            "'2026-01-01T00:00:00', '')")


# ─── 迁移 ────────────────────────────────────────────────────


class TestMigration:

    def test_current_version_covers_locked_column(self):
        assert CURRENT_VERSION >= 8

    def test_locked_column_exists_and_defaults_to_zero(self, db: ProfileDB):
        db.upsert("u1", "quota", "k", 1)
        cols = {
            row[1]: row
            for row in db._borrow_connection().execute(
                "PRAGMA table_info(profile_entries)").fetchall()
        }
        assert "locked" in cols

    def test_v7_database_upgrades_in_place(self, tmp_path: Path):
        """v7 老库（无 locked 列）打开后自动补列，已有数据与版本号都正确。"""
        db_path = tmp_path / "v7.db"
        _make_v7_db(db_path)

        with sqlite3.connect(db_path) as conn:
            cols = {row[1] for row in conn.execute(
                "PRAGMA table_info(profile_entries)").fetchall()}
        assert "locked" not in cols

        upgraded = ProfileDB(db_path)

        assert upgraded.get_entry("u1", "quota", "k")["value"] == 5
        assert upgraded.get_entry("u1", "quota", "k")["updated_at"] == "2026-01-01T00:00:00"
        with sqlite3.connect(db_path) as conn:
            cols = {row[1] for row in conn.execute(
                "PRAGMA table_info(profile_entries)").fetchall()}
            version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        assert "locked" in cols
        assert version == CURRENT_VERSION

        # 升级后的库可以正常上锁
        assert upgraded.set_locked("u1", "quota", "k") is True

    def test_reopen_after_upgrade_is_idempotent(self, tmp_path: Path):
        db_path = tmp_path / "v7b.db"
        _make_v7_db(db_path)
        ProfileDB(db_path)
        again = ProfileDB(db_path)
        assert again.get_entry("u1", "quota", "k")["value"] == 5
        assert "locked" not in again.get_entry("u1", "quota", "k")


# ─── 锁定 / 解锁 ─────────────────────────────────────────────


class TestLockState:

    def test_lock_existing_entry_keeps_value(self, db: ProfileDB):
        db.upsert("u1", "quota", "k", 1, updated_at="2026-01-01T00:00:00")
        assert db.set_locked("u1", "quota", "k") is True
        entry = db.get_entry("u1", "quota", "k")
        assert entry["locked"] is True
        assert entry["value"] == 1
        assert entry["updated_at"] == "2026-01-01T00:00:00"

    def test_lock_missing_entry_creates_placeholder(self, db: ProfileDB):
        """从未写过的 key 也能锁：占位行不带任何值。"""
        assert db.set_locked("u1", "quota", "never") is True
        entry = db.get_entry("u1", "quota", "never")
        assert entry["locked"] is True
        assert entry["value"] == 0
        assert entry["updated_at"] == ""

    def test_lock_is_idempotent(self, db: ProfileDB):
        assert db.set_locked("u1", "quota", "k") is True
        assert db.set_locked("u1", "quota", "k") is False

    def test_unlock_restores_writability(self, db: ProfileDB):
        db.upsert("u1", "quota", "k", 1)
        db.set_locked("u1", "quota", "k")
        assert db.unlock("u1", "quota", "k") is True
        assert db.unlock("u1", "quota", "k") is False
        db.upsert("u1", "quota", "k", 2)
        assert db.get_entry("u1", "quota", "k")["value"] == 2

    def test_lock_is_per_user(self, db: ProfileDB):
        db.upsert("u1", "quota", "k", 1)
        db.upsert("u2", "quota", "k", 1)
        db.set_locked("u1", "quota", "k")
        assert db.is_locked("u1", "quota", "k") is True
        assert db.is_locked("u2", "quota", "k") is False

    def test_get_all_exposes_locked_only_when_set(self, db: ProfileDB):
        db.upsert("u1", "quota", "free", 1)
        db.upsert("u1", "quota", "locked", 1)
        db.set_locked("u1", "quota", "locked")
        data = db.get_all("u1")
        assert "locked" not in data["quota"]["free"]
        assert data["quota"]["locked"]["locked"] is True


# ─── 写入挡板 ────────────────────────────────────────────────


class TestWriteGuards:

    def test_upsert_refuses_locked_entry(self, db: ProfileDB):
        db.upsert("u1", "quota", "k", 1)
        db.set_locked("u1", "quota", "k")
        db.upsert("u1", "quota", "k", 99)
        assert db.get_entry("u1", "quota", "k")["value"] == 1

    def test_upsert_keeps_lock_flag(self, db: ProfileDB):
        """upsert 不能像 INSERT OR REPLACE 那样把 locked 清掉。"""
        db.upsert("u1", "quota", "free", 1)
        db.set_locked("u1", "quota", "free")
        db.unlock("u1", "quota", "free")
        db.upsert("u1", "quota", "free", 5)
        assert db.get_entry("u1", "quota", "free")["value"] == 5
        assert not db.get_entry("u1", "quota", "free").get("locked")

    def test_upsert_many_skips_only_locked_entry(self, db: ProfileDB):
        db.upsert("u1", "quota", "locked", 1)
        db.set_locked("u1", "quota", "locked")
        db.upsert_many("u1", [
            ("quota", "locked", 99, None, "tick", 98),
            ("quota", "free", 5, None, "tick", 5),
        ])
        assert db.get_entry("u1", "quota", "locked")["value"] == 1
        assert db.get_entry("u1", "quota", "free")["value"] == 5

    def test_cas_update_returns_false_for_locked(self, db: ProfileDB):
        db.upsert("u1", "quota", "k", 1, updated_at="2026-01-01T00:00:00")
        db.set_locked("u1", "quota", "k")
        updated = db.update_if_current(
            "u1", "quota", "k", expected_value=1,
            expected_updated_at="2026-01-01T00:00:00", new_value=2)
        assert updated is False
        assert db.get_entry("u1", "quota", "k")["value"] == 1

    def test_undo_history_refused_for_locked(self, db: ProfileDB):
        db.upsert("u1", "quota", "k", 1, change_type="action")
        db.upsert("u1", "quota", "k", 2, change_type="action")
        history_id = db.get_history("u1")[0]["id"]
        db.set_locked("u1", "quota", "k")
        assert db.undo_history(history_id) is False
        assert db.get_entry("u1", "quota", "k")["value"] == 2

    def test_rename_keys_keeps_lock(self, db: ProfileDB):
        db.upsert("u1", "quota", "old", 1)
        db.set_locked("u1", "quota", "old")
        db.rename_keys(
            [("quota", "old", "new")], operation_id="op1",
            save_references=lambda: None)
        entry = db.get_entry("u1", "quota", "new")
        assert entry["value"] == 1
        assert entry.get("locked") is True


# ─── 服务层：读返回空、写被拒绝 ─────────────────────────────


class TestServiceSemantics:

    def test_profile_read_returns_none_for_locked(self, profile_env):
        from lvjiang.core.profile.repository import db_set_locked, db_upsert
        from lvjiang.core.profile.service import profile_read

        db_upsert(profile_env.username, "quota", "weekly_quota", 1)
        assert profile_read(profile_env.username, "weekly_quota") == 1
        db_set_locked(profile_env.username, "quota", "weekly_quota")
        assert profile_read(profile_env.username, "weekly_quota") is None

    def test_profile_is_locked_reports_state(self, profile_env):
        from lvjiang.core.profile.repository import db_set_locked
        from lvjiang.core.profile.service import profile_is_locked

        assert profile_is_locked(profile_env.username, "weekly_quota") is False
        db_set_locked(profile_env.username, "quota", "weekly_quota")
        assert profile_is_locked(profile_env.username, "weekly_quota") is True

    def test_profile_action_refuses_and_logs_error(self, profile_env, monkeypatch):
        import lvjiang.core.profile.service as service
        from lvjiang.core.profile.repository import db_read_entry, db_set_locked

        db_set_locked(profile_env.username, "quota", "weekly_quota")
        errors: list[str] = []
        monkeypatch.setattr(service.logger, "error", errors.append)

        result = service.profile_action(
            profile_env.username, "weekly_quota", delta=1, source="测试")
        assert result == 0
        assert db_read_entry(profile_env.username, "quota", "weekly_quota")["value"] == 0
        assert errors and "已锁定" in errors[0]

    def test_note_write_refused_for_locked(self, profile_env):
        from lvjiang.core.profile.repository import (
            db_read_entry,
            db_set_locked,
        )
        from lvjiang.core.profile.service import profile_action

        db_set_locked(profile_env.username, "note", "user_note")
        profile_action(
            profile_env.username, "user_note", set_value="新文本", source="测试")
        assert db_read_entry(
            profile_env.username, "note", "user_note")["value_text"] == ""


    def test_profile_observe_reports_locked_instead_of_accepted(self, profile_env):
        """观测写入被锁拒绝时必须报 locked，不能把"什么都没写"报成 accepted。"""
        from lvjiang.core.profile.repository import db_set_locked, db_upsert
        from lvjiang.core.profile.service import profile_observe

        db_upsert(profile_env.username, "quota", "weekly_quota", 1)
        db_set_locked(profile_env.username, "quota", "weekly_quota")

        result = profile_observe(profile_env.username, "weekly_quota", 2)
        assert result["accepted"] is False
        assert result["reason"] == "locked"

    def test_profile_read_all_hides_locked_value(self, profile_env):
        """profile_all 的口径要和 profile_get 一致：锁定项没有值。"""
        from lvjiang.core.profile.repository import db_set_locked, db_upsert
        from lvjiang.core.profile.service import profile_read_all

        db_upsert(profile_env.username, "quota", "weekly_quota", 1)
        db_set_locked(profile_env.username, "quota", "weekly_quota")

        entry = profile_read_all(
            profile_env.username)["quota"]["weekly_quota"]
        assert entry["locked"] is True
        assert entry["value"] is None

    def test_profile_read_all_skips_regen_computation_for_locked(self, profile_env):
        """锁定 regen 不能每读一次就算出一个"活"的当前值。"""
        from lvjiang.core.profile.repository import db_set_locked, db_upsert
        from lvjiang.core.profile.service import profile_read_all

        db_upsert(profile_env.username, "regen", "energy", 50,
                  updated_at="2020-01-01T00:00:00")
        db_set_locked(profile_env.username, "regen", "energy")

        entry = profile_read_all(profile_env.username)["regen"]["energy"]
        assert entry["locked"] is True
        assert entry["value"] is None

    def test_sync_adapter_returns_none_for_locked_target(self, profile_env):
        """同步目标锁定时按"写入失败"返回 None，让引擎中止下游递归。"""
        from lvjiang.core.profile.repository import db_set_locked
        from lvjiang.core.profile.service import sync_write_adapter

        db_set_locked(profile_env.username, "stock", "target_stock")
        assert sync_write_adapter(
            profile_env.username, "stock", "target_stock", delta=3) is None


# ─── 引擎：锁定不重置、不告警 ───────────────────────────────


class TestEngineSkipsLocked:

    def test_quota_reset_skipped_for_locked(self, profile_env):
        from lvjiang.core.profile.engine import ProfileProcessor
        from lvjiang.core.profile.repository import (
            db_read_entry,
            db_set_locked,
            db_upsert,
        )

        db_upsert(profile_env.username, "quota", "weekly_quota", 1,
                  updated_at="2020-01-01T00:00:00")  # 早于任何周期边界
        db_set_locked(profile_env.username, "quota", "weekly_quota")

        processor = ProfileProcessor(
            SimpleNamespace(list_users=lambda: [profile_env.username]))
        processor.tick()

        entry = db_read_entry(profile_env.username, "quota", "weekly_quota")
        assert entry["value"] == 1
        assert entry["updated_at"] == "2020-01-01T00:00:00"

    def test_regen_and_alert_skipped_for_locked(self, profile_env):
        from lvjiang.core.profile.engine import ProfileProcessor
        from lvjiang.core.profile.repository import (
            db_read_entry,
            db_set_locked,
            db_upsert,
        )

        db_upsert(profile_env.username, "regen", "energy", 95,
                  updated_at="2020-01-01T00:00:00")
        db_set_locked(profile_env.username, "regen", "energy")

        alerts: list[tuple] = []
        processor = ProfileProcessor(
            SimpleNamespace(list_users=lambda: [profile_env.username]),
            notify_alert=lambda *args: alerts.append(args))
        processor.tick()

        entry = db_read_entry(profile_env.username, "regen", "energy")
        assert entry["value"] == 95
        assert entry["updated_at"] == "2020-01-01T00:00:00"
        assert alerts == []


# ─── 总览显示 ───────────────────────────────────────────────


class TestOverviewRendering:

    def test_locked_cell_is_blank_with_locked_style(self):
        from lvjiang.core.profile.models import KeyDef
        from lvjiang.ui.profile.cell_formatting import format_profile_cell

        kd = KeyDef(key="k", label="配额")
        data = {"quota": {"k": {"value": 2, "locked": True, "updated_at": ""}}}
        assert format_profile_cell(kd, "quota", data) == ("", "locked")

    def test_tooltip_explains_lock(self):
        from lvjiang.core.profile.models import KeyDef
        from lvjiang.ui.profile.cell_formatting import format_cell_tooltip

        kd = KeyDef(key="k", label="配额")
        data = {"quota": {"k": {"value": 2, "locked": True, "updated_at": ""}}}
        tooltip = format_cell_tooltip(kd, "quota", data)
        assert "锁定" in tooltip

    def test_totals_exclude_locked_users(self):
        from lvjiang.core.profile.models import QuotaKeyDef
        from lvjiang.ui.profile.cell_formatting import sum_profile_column

        kd = QuotaKeyDef(key="k", label="配额", cap=5)
        users = {
            "u1": {"quota": {"k": {"value": 2}}},
            "u2": {"quota": {"k": {"value": 3, "locked": True}}},
        }
        assert sum_profile_column(kd, "quota", users) == "2"
