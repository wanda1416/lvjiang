"""set-only 账本：迁移身份、读时配额、离线消耗、事件时间仲裁。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from lvjiang.core.profile import repository, schema, service
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
        row = conn.execute("SELECT op_id, local_id FROM profile_history").fetchone()
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
    entry = {"value": 9, "event_at": yesterday.isoformat(timespec="seconds")}
    assert service._quota_display_value(entry, kd) == 0


def test_offset_timestamp_does_not_break_quota_read() -> None:
    kd = QuotaKeyDef(key="daily", label="日", period="day", reset_time="05:00")
    recent = datetime.now().replace(microsecond=0).isoformat(timespec="seconds")
    entry = {"value": 4, "event_at": recent + "+08:00"}
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


def test_unlock_keeps_value_frozen(tmp_path, monkeypatch) -> None:
    """解锁只解除标记：值保持锁定时的样子，不用账本重建（否则解锁瞬间跳变）。"""
    monkeypatch.setattr(repository, "_DB_PATH", tmp_path / "profile.db")
    repository.reset_profile_db()
    repository.db_upsert("u", "stock", "k", 3, op_type="set",
                         event_at="2026-11-01T00:00:00",
                         history_ts="2026-11-01T00:00:00")
    assert repository.db_set_locked("u", "stock", "k")
    conn = sqlite3.connect(tmp_path / "profile.db")
    try:
        conn.execute(
            "INSERT INTO profile_history "
            "(event_at, username, type, key, old_value, new_value, op_type, "
            "instance_id, local_id, op_id) "
            "VALUES ('2026-11-12T10:00:00', 'u', 'stock', 'k', 3, 9, 'set', "
            "'phone', 3, 'later-op')")
        conn.commit()
    finally:
        conn.close()
    assert repository.db_unlock("u", "stock", "k")
    entry = repository.db_read_entry("u", "stock", "k")
    assert entry["value"] == 3
    assert "locked" not in entry


def test_inc_persists_without_a_prior_sync(tmp_path, monkeypatch) -> None:
    """消耗当时落库，不要求本进程先完成一次拉取。"""
    monkeypatch.setattr(repository, "_DB_PATH", tmp_path / "profile.db")
    repository.reset_profile_db()
    schema._PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    schema._PROFILE_PATH.write_text(
        "regen:\n"
        "  - {key: xinli, label: 心力, regen_type: realtime,\n"
        "     regen_rate_value: 0.125, regen_rate_unit: minute, cap: 600}\n",
        encoding="utf-8",
    )
    schema._config = None
    repository.db_upsert(
        "u", "regen", "xinli", 10,
        event_at=datetime.now().isoformat(timespec="seconds"),
    )
    written = service.profile_action("u", "xinli", delta=-1, source="消耗")
    assert abs(written - 9) < 0.02
    history = repository.db_get_history("u", "regen", "xinli")
    assert history[0]["op_type"] == "set"
    assert abs(history[0]["new_value"] - 9) < 0.02
    assert history[0]["source"] == "消耗"


def test_reopen_abandoned_returns_only_this_device_sets(tmp_path, monkeypatch) -> None:
    """时间规则收紧后标成 -1 的本机 set/delta 要能再判一次。"""
    monkeypatch.setattr(repository, "_DB_PATH", tmp_path / "profile.db")
    repository.reset_profile_db()
    db = repository.ProfileDB(tmp_path / "profile.db")
    conn = sqlite3.connect(tmp_path / "profile.db")
    try:
        conn.executemany(
            "INSERT INTO profile_history "
            "(event_at, username, type, key, new_value, op_type, instance_id, "
            "local_id, op_id, remote_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                ("2026-11-04T08:23:56", "u", "stock", "k", 1, "set", "dev",
                 9, "legacy:dev:9", -1),
                ("2026-11-04T08:23:57", "u", "stock", "k", 2, "tick", "dev",
                 10, "tick-row", -1),
                ("2026-11-04T08:23:58", "u", "stock", "k", 3, "set", "other",
                 11, "legacy:other:11", -1),
                ("2026-11-04T08:23:59", "u", "stock", "k", 4, "set", "dev",
                 12, "unscoped:12", -1),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    db.reopen_abandoned("dev")
    conn = sqlite3.connect(tmp_path / "profile.db")
    try:
        rows = conn.execute(
            "SELECT op_id, remote_id FROM profile_history ORDER BY local_id"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [
        ("legacy:dev:9", None),
        ("tick-row", -1),
        ("legacy:other:11", -1),
        ("unscoped:12", -1),
    ]
