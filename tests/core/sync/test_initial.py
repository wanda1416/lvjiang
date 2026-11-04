"""初始同步的本地备份与状态辅助函数。"""
from __future__ import annotations


def test_backup_moves_users_profile_db_and_yaml(monkeypatch, tmp_path):
    from lvjiang.core.sync import initial

    session = tmp_path / "session"
    monkeypatch.setattr(initial, "SESSION_CONFIG_DIR", session)
    monkeypatch.setattr(initial, "USERS_DIR", session / "users")

    users = session / "users"
    users.mkdir(parents=True)
    (users / "甲.json").write_text("{}", encoding="utf-8")
    (session / "profile.db").write_text("db", encoding="utf-8")
    (session / "profile.yaml").write_text("yaml", encoding="utf-8")

    backup = initial.backup_local_profile_data()

    assert backup is not None and backup.exists()
    assert (backup / "users" / "甲.json").exists()
    assert (backup / "profile.db").exists()
    assert (backup / "profile.yaml").exists()
    # 源位置清空，users 目录重建为可用空目录
    assert not (users / "甲.json").exists()
    assert users.is_dir()
    assert not (session / "profile.db").exists()
    assert not (session / "profile.yaml").exists()


def test_backup_noop_when_nothing_to_move(monkeypatch, tmp_path):
    from lvjiang.core.sync import initial

    session = tmp_path / "session"
    monkeypatch.setattr(initial, "SESSION_CONFIG_DIR", session)
    monkeypatch.setattr(initial, "USERS_DIR", session / "users")

    assert initial.backup_local_profile_data() is None
    assert not session.exists() or not any(session.iterdir())
