"""初始同步三态：播种、只补快照、跟随失败还原。

这些用例把会话目录指到 tmp_path。account.json、session.json 和 profile.db
都不得落到仓库的 config/session。
"""
from __future__ import annotations

import sqlite3

import pytest

from lvjiang.core.sync import initial


@pytest.fixture(autouse=True)
def _drop_session_store():
    """避免临时目录的 SessionStore 在用例结束后还指着真实 session.json。"""
    yield
    from lvjiang.core.config.session import reset_session_store
    reset_session_store()


class _Step:
    def __init__(self, *, errors=None, pushed=0, pulled=0, snapshots=0,
                 documents=False):
        self.errors = list(errors or [])
        self.pushed = [] if documents else pushed
        self.pulled = [] if documents else pulled
        self.snapshots = snapshots


def _isolate(monkeypatch, tmp_path):
    """把初始同步会写到的路径全部换到临时目录。"""
    from lvjiang import constants
    from lvjiang.core.account import service
    from lvjiang.core.config.session import reset_session_store

    session = tmp_path / "session"
    session.mkdir()
    monkeypatch.setattr(constants, "SESSION_CONFIG_DIR", session)
    monkeypatch.setattr(constants, "SESSION_PATH", session / "session.json")
    monkeypatch.setattr(constants, "USERS_DIR", session / "users")
    monkeypatch.setattr(service, "SESSION_CONFIG_DIR", session)
    monkeypatch.setattr(initial, "SESSION_CONFIG_DIR", session)
    monkeypatch.setattr(initial, "USERS_DIR", session / "users")
    reset_session_store()
    return session


def _install_channels(monkeypatch, *, definition=None, users=None, documents=None,
                      snapshot=None, pull=None):
    from lvjiang.core.profile import snapshot_sync
    from lvjiang.core.sync import definition_sync, engine, users_sync

    monkeypatch.setattr(initial, "local_profile_has_entries", lambda: False)
    monkeypatch.setattr(definition_sync, "sync_definition",
                        definition or (lambda *a, **k: _Step()))
    monkeypatch.setattr(users_sync, "sync_users", users or (lambda *a, **k: _Step()))
    monkeypatch.setattr(engine, "sync_documents",
                        documents or (lambda *a, **k: _Step(documents=True)))
    monkeypatch.setattr(snapshot_sync, "refresh_snapshot",
                        snapshot or (lambda *a, **k: _Step(snapshots=1)))
    monkeypatch.setattr(snapshot_sync, "pull_and_replay",
                        pull or (lambda *a, **k: _Step()))


def test_classify_empty_baseline_with_local_entries_publishes():
    state = {"snapshot_count": 1, "history": 0, "snapshot": {
        "entry_count": 0, "snapshot_remote_id": 0, "origin_name": "书房"}}
    assert initial.classify_initial_action(state, local_has_entries=True) == "replace_empty"
    assert initial.classify_initial_action(state, local_has_entries=False) == "follow"


def test_classify_without_snapshot_object_follows():
    """老响应没有 snapshot 对象时不把「有快照」误判成空基线。"""
    state = {"snapshot_count": 1, "history": 0}
    assert initial.classify_initial_action(state, local_has_entries=True) == "follow"


def test_seed_does_not_snapshot_when_roster_upload_fails(monkeypatch, tmp_path):
    session = _isolate(monkeypatch, tmp_path)
    calls: list[str] = []

    def post(route, data, token):
        if route == "/api/sync/state":
            return {"snapshot_count": 0, "not_inited": True, "users": 0,
                    "documents": 0, "definition": False, "history": 0, "empty": True,
                    "snapshot": None}
        if route == "/api/sync/claim_init":
            return {"status": "owner", "seed_owner": True}
        raise AssertionError(route)

    def users(*_args, **_kwargs):
        calls.append("users")
        return _Step(errors=["上行失败"])

    def snapshot(*_args, **_kwargs):
        calls.append("snapshot")
        return _Step(snapshots=1)

    monkeypatch.setattr(initial, "post", post)
    _install_channels(monkeypatch, users=users, snapshot=snapshot)
    monkeypatch.setattr(initial, "set_sync_established",
                        lambda _value: calls.append("established"))

    report = initial.initial_sync("t")

    assert calls == ["users"]
    assert report.established is False
    assert not list(session.glob("_backup-initialsync-*"))


def test_takeover_without_entries_does_not_write_empty_snapshot(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    calls: list[str] = []

    def post(route, data, token):
        if route == "/api/sync/state":
            return {"snapshot_count": 0, "not_inited": False, "users": 2,
                    "documents": 0, "definition": True, "history": 0, "empty": False,
                    "snapshot": None}
        if route == "/api/sync/claim_init":
            return {"status": "owner", "seed_owner": False}
        raise AssertionError(route)

    def snapshot(token, *, allow_empty=False):
        calls.append(f"snapshot:{allow_empty}")
        return _Step(snapshots=1)

    monkeypatch.setattr(initial, "post", post)
    _install_channels(monkeypatch, snapshot=snapshot)

    report = initial.initial_sync("t")

    assert report.established is False
    assert calls == []
    assert any("没有总览数据" in error for error in report.errors)


def test_takeover_with_entries_refreshes_snapshot(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    calls: list[str] = []

    def post(route, data, token):
        if route == "/api/sync/state":
            return {"snapshot_count": 0, "not_inited": False, "users": 2,
                    "documents": 0, "definition": True, "history": 0, "empty": False,
                    "snapshot": None}
        if route == "/api/sync/claim_init":
            return {"status": "owner", "seed_owner": False}
        raise AssertionError(route)

    def snapshot(token, *, allow_empty=False):
        calls.append(f"snapshot:{allow_empty}")
        return _Step(snapshots=1)

    monkeypatch.setattr(initial, "post", post)
    _install_channels(monkeypatch, snapshot=snapshot)
    monkeypatch.setattr(initial, "local_profile_has_entries", lambda: True)
    monkeypatch.setattr(initial, "set_sync_established", lambda _value: None)

    report = initial.initial_sync("t")

    assert report.established is True
    assert calls == ["snapshot:False"]


def test_busy_claim_does_not_upload(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    calls: list[str] = []

    def post(route, data, token):
        if route == "/api/sync/state":
            return {"snapshot_count": 0, "users": 0, "documents": 0,
                    "definition": False, "history": 0, "snapshot": None}
        return {"status": "busy"}

    monkeypatch.setattr(initial, "post", post)
    _install_channels(
        monkeypatch,
        definition=lambda *a, **k: calls.append("definition") or _Step(),
    )

    report = initial.initial_sync("t")

    assert calls == []
    assert any("另一台电脑" in error for error in report.errors)
    assert report.established is False


def test_follow_failure_restores_users_and_database(monkeypatch, tmp_path):
    session = _isolate(monkeypatch, tmp_path)
    users = session / "users"
    users.mkdir()
    (users / "甲.json").write_text('{"name":"甲"}\n', encoding="utf-8")
    (session / "profile.yaml").write_text("local: true\n", encoding="utf-8")
    (session / "session.json").write_text('{"users": ["甲"]}\n', encoding="utf-8")
    (session / "interface.json").write_text(
        '{"actives": {"user": "甲"}}\n', encoding="utf-8")
    database = session / "profile.db"
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE marker(n INTEGER)")
        connection.execute("INSERT INTO marker VALUES (7)")
        connection.commit()
    finally:
        connection.close()

    def post(route, data, token):
        return {"snapshot_count": 1, "users": 1, "documents": 0, "definition": True,
                "history": 4, "empty": False, "snapshot": {
                    "origin_name": "书房PC", "created_at": "2026-11-06T01:00:00Z",
                    "entry_count": 3, "snapshot_remote_id": 4}}

    def definition(*_args, **_kwargs):
        (session / "profile.yaml").write_text("cloud: true\n", encoding="utf-8")
        return _Step()

    def users_step(*_args, **_kwargs):
        (session / "users").mkdir(exist_ok=True)
        (session / "users" / "云.json").write_text("{}\n", encoding="utf-8")
        (session / "session.json").write_text('{"users": ["云"]}\n', encoding="utf-8")
        return _Step(pulled=1)

    def pull(*_args, **_kwargs):
        database.write_bytes(b"not a database")
        return _Step(errors=["拉取失败"])

    monkeypatch.setattr(initial, "post", post)
    _install_channels(monkeypatch, definition=definition, users=users_step, pull=pull)

    report = initial.initial_sync("t", confirmed="follow")

    assert report.established is False
    assert (users / "甲.json").read_text(encoding="utf-8").startswith("{")
    assert not (users / "云.json").exists()
    assert (session / "profile.yaml").read_text(encoding="utf-8") == "local: true\n"
    assert '"甲"' in (session / "session.json").read_text(encoding="utf-8")
    assert "甲" in (session / "interface.json").read_text(encoding="utf-8")
    checked = sqlite3.connect(database)
    try:
        assert checked.execute("SELECT n FROM marker").fetchone()[0] == 7
    finally:
        checked.close()
    assert not any(path.name.startswith("_backup-initialsync-") for path in session.iterdir())

    # 再失败一次：云端用户不能残留在已经还原的名册里。
    again = initial.initial_sync("t", confirmed="follow")
    assert again.established is False
    assert not (users / "云.json").exists()
    checked = sqlite3.connect(database)
    try:
        assert checked.execute("SELECT n FROM marker").fetchone()[0] == 7
    finally:
        checked.close()


def test_follow_rejects_empty_roster_when_cloud_has_users(monkeypatch, tmp_path):
    session = _isolate(monkeypatch, tmp_path)
    (session / "users").mkdir()
    (session / "users" / "甲.json").write_text("{}\n", encoding="utf-8")
    (session / "session.json").write_text('{"users": ["甲"]}\n', encoding="utf-8")

    def post(route, data, token):
        return {"snapshot_count": 1, "users": 2, "history": 1, "snapshot": {
            "entry_count": 1, "snapshot_remote_id": 1, "origin_name": "PC",
            "created_at": "T"}}

    monkeypatch.setattr(initial, "post", post)
    _install_channels(monkeypatch)

    report = initial.initial_sync("t", confirmed="follow")

    assert report.established is False
    assert any("名册仍为空" in error for error in report.errors)
    assert '"甲"' in (session / "session.json").read_text(encoding="utf-8")


def test_unconfirmed_follow_does_not_move_local_files(monkeypatch, tmp_path):
    session = _isolate(monkeypatch, tmp_path)
    users = session / "users"
    users.mkdir()
    (users / "甲.json").write_text("{}\n", encoding="utf-8")

    def post(route, data, token):
        if route == "/api/sync/claim_init":
            raise AssertionError("未确认不能占锁或跟随")
        return {"snapshot_count": 1, "users": 1, "history": 1, "snapshot": {
            "entry_count": 2, "snapshot_remote_id": 3, "origin_name": "PC",
            "created_at": "T"}}

    monkeypatch.setattr(initial, "post", post)
    _install_channels(monkeypatch)

    report = initial.initial_sync("t", confirmed="seed")

    assert report.established is False
    assert (users / "甲.json").is_file()
    assert not any(path.name.startswith("_backup-initialsync-") for path in session.iterdir())
    assert any("确认后再继续" in error for error in report.errors)
