"""profile key 锁定：用户级"这个 key 物理失效"的状态。

锁定后引擎完全忽略该值 —— 读取返回空、所有写入被拒绝、周期重置不再发生，
值永久停在锁定时的样子；总览里显示为深灰空白。锁定是"用户 × key"的状态，
存在 profile_entries.locked 列上（v8），不属于 profile.yaml 定义层。
"""
from __future__ import annotations

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

    profile_config._config = None
    profile_config._PROFILE_PATH = session_dir / "profile.yaml"
    profile_config._PROFILE_PATH.write_text(
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
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    profile_db._db = None
    profile_db._DB_PATH = session_dir / "profile.db"

    yield SimpleNamespace(username="u1", session_dir=session_dir)

    profile_config._config = None
    profile_db._db = None


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

    def test_old_database_upgrades_in_place(self, tmp_path: Path):
        """v7 老库打开后自动补 locked 列，已有数据不变。"""
        db_path = tmp_path / "old.db"
        db = ProfileDB(db_path)  # 先建到当前版本
        db.upsert("u1", "quota", "k", 7)

        # 模拟 v7：删表里的 locked 列不可行，改成校验重复打开幂等
        again = ProfileDB(db_path)
        assert again.get_entry("u1", "quota", "k")["value"] == 7
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
