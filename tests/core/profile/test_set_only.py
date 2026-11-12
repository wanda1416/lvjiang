"""set-only 账本：迁移身份、读时配额、消耗闸门、事件时间仲裁。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from lvjiang.core.profile import regen_gate, repository, schema, service
from lvjiang.core.profile.arbitrate import is_strictly_newer
from lvjiang.core.profile.models import QuotaKeyDef


def test_replayed_row_keeps_origin_identity(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "profile.db"
    monkeypatch.setattr(repository, "_DB_PATH", db_path)
    original = repository.MIGRATIONS
    monkeypatch.setattr(repository, "MIGRATIONS", original[:10])
    monkeypatch.setattr(repository, "CURRENT_VERSION", 10)
    repository.reset_profile_db()
    repository.ProfileDB(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO profile_history "
            "(ts, username, type, key, old_value, new_value, change_type, device_id, "
            "origin_local_id) "
            "VALUES ('2026-11-12T09:00:00', 'u', 'stock', 'k', 1, 2, 'set', 'phone', 42)")
        conn.execute(
            "INSERT INTO profile_entries (username, type, key, value, updated_at) "
            "VALUES ('u', 'stock', 'k', 2, '2026-11-12T08:00:00')")
        conn.commit()
    finally:
        conn.close()
    repository.reset_profile_db()
    monkeypatch.setattr(repository, "MIGRATIONS", original)
    monkeypatch.setattr(repository, "CURRENT_VERSION", original[-1][0])
    repository.ProfileDB(db_path)
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT op_id, id FROM profile_history").fetchone()
    finally:
        conn.close()
    assert row[0] == "legacy:phone:42"
    assert row[0] != f"legacy:phone:{row[1]}"


def test_newer_event_wins_and_empty_op_id_loses() -> None:
    assert is_strictly_newer("2026-11-12T10:00:00", "b", "2026-11-12T09:00:00", "a")
    assert not is_strictly_newer("2026-11-12T10:00:00", "", "2026-11-12T10:00:00", "a")
    assert not is_strictly_newer(
        "2026-11-12T10:00:00", "a", "2026-11-12T10:00:00+08:00", "b")
    assert is_strictly_newer(
        "2026-11-12T10:00:00", "b", "2026-11-12T10:00:00+08:00", "a")


def test_quota_read_is_zero_after_period_boundary() -> None:
    kd = QuotaKeyDef(key="daily", label="日", period="day", reset_time="05:00")
    yesterday = (datetime.now() - timedelta(days=2)).replace(microsecond=0)
    entry = {"value": 9, "updated_at": yesterday.isoformat(timespec="seconds")}
    assert service._quota_display_value(entry, kd) == 0


def test_offset_timestamp_does_not_break_quota_read() -> None:
    kd = QuotaKeyDef(key="daily", label="日", period="day", reset_time="05:00")
    recent = datetime.now().replace(microsecond=0).isoformat(timespec="seconds")
    entry = {"value": 4, "updated_at": recent + "+08:00"}
    assert service._quota_display_value(entry, kd) == 4


def test_unscoped_history_still_rewrites_entry(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "profile.db"
    monkeypatch.setattr(repository, "_DB_PATH", db_path)
    original = repository.MIGRATIONS
    monkeypatch.setattr(repository, "MIGRATIONS", original[:10])
    monkeypatch.setattr(repository, "CURRENT_VERSION", 10)
    repository.reset_profile_db()
    repository.ProfileDB(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO profile_history "
            "(ts, username, type, key, old_value, new_value, change_type) "
            "VALUES ('2026-11-12T09:00:00', 'u', 'stock', 'k', 1, 7, 'set')")
        conn.execute(
            "INSERT INTO profile_entries (username, type, key, value, updated_at) "
            "VALUES ('u', 'stock', 'k', 1, '2026-11-01T00:00:00')")
        conn.commit()
    finally:
        conn.close()
    repository.reset_profile_db()
    monkeypatch.setattr(repository, "MIGRATIONS", original)
    monkeypatch.setattr(repository, "CURRENT_VERSION", original[-1][0])
    repository.ProfileDB(db_path)
    conn = sqlite3.connect(db_path)
    try:
        op_id, value = conn.execute(
            "SELECT h.op_id, e.value FROM profile_history h "
            "JOIN profile_entries e ON e.username=h.username AND e.key=h.key"
        ).fetchone()
    finally:
        conn.close()
    assert op_id.startswith("unscoped:")
    assert value == 7


def test_unlock_rebuilds_entry_from_latest_op(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(repository, "_DB_PATH", tmp_path / "profile.db")
    repository.reset_profile_db()
    repository.db_upsert("u", "stock", "k", 3, change_type="set",
                         updated_at="2026-11-01T00:00:00",
                         history_ts="2026-11-01T00:00:00")
    assert repository.db_set_locked("u", "stock", "k")
    conn = sqlite3.connect(tmp_path / "profile.db")
    try:
        conn.execute(
            "INSERT INTO profile_history "
            "(ts, username, type, key, old_value, new_value, change_type, "
            "device_id, origin_local_id, op_id) "
            "VALUES ('2026-11-12T10:00:00', 'u', 'stock', 'k', 3, 9, 'set', "
            "'phone', 3, 'later-op')")
        conn.commit()
    finally:
        conn.close()
    assert repository.db_unlock("u", "stock", "k")
    entry = repository.db_read_entry("u", "stock", "k")
    assert entry["value"] == 9
    assert entry["op_id"] == "later-op"
    assert "locked" not in entry


def test_absolute_write_is_allowed_when_inc_is_gated(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(repository, "_DB_PATH", tmp_path / "profile.db")
    repository.reset_profile_db()
    regen_gate.reset()
    monkeypatch.setattr(regen_gate, "_session_state", lambda: True)
    schema._PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    schema._PROFILE_PATH.write_text(
        "stock:\n  - {key: coin, label: 币}\n", encoding="utf-8")
    schema._config = None
    with pytest.raises(service.ProfileOperationRefused):
        service.profile_action("u", "coin", delta=1)
    written = service.profile_action("u", "coin", set_value=4, source="观测")
    assert written == 4
    entry = repository.db_read_entry("u", "stock", "coin")
    assert entry["value"] == 4
    assert entry["op_id"]
    history = repository.db_get_history("u", "stock", "coin")
    assert history[0]["change_type"] == "set"
    assert not history[0]["op_id"].startswith("unscoped:")
