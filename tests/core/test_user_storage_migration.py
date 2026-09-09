import json

from lvjiang.core.config.session import reset_session_store
from lvjiang.core.user_config import UserConfigManager


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_legacy_users_sessions_and_batch_rows_migrate_once(tmp_path, monkeypatch):
    from lvjiang import constants

    session_path = tmp_path / "session.json"
    users_dir = tmp_path / "users"
    monkeypatch.setattr(constants, "SESSION_PATH", session_path)
    monkeypatch.setattr(constants, "USERS_DIR", users_dir)
    _write_json(session_path, {
        "users": [
            {"name": "内部A", "created_at": "2025-01-01", "avatar": ""},
            {"name": "角色B", "created_at": "2025-02-01", "avatar": ""},
        ],
        "active_user": "内部A",
        "batch": {
            "active_config": "日常",
            "script_ids": ["task_a"],
            "enabled_rows": {"日常": [True, False]},
            "configs": {
                "日常": {
                    "name": "日常",
                    "columns": ["username", "account", "role", "role_index", "tail"],
                    "user_column": "username",
                    "rows": [
                        {"username": "内部A", "account": "账号一", "role": "角色A",
                         "role_index": "1", "tail": "1111"},
                        {"username": "角色B", "account": "账号一", "role": "角色B",
                         "role_index": "2", "tail": "1111"},
                    ],
                    "workflows": {"prepare_item": "batch/prepare_item.wf"},
                }
            },
        },
    })
    _write_json(users_dir / "内部A.json", {"current_user": "内部A", "score": 7})
    reset_session_store()

    manager = UserConfigManager()
    assert manager.list_users() == ["内部A", "角色B"]
    assert manager.get_user("内部A").attributes == {
        "account": "账号一", "role": "角色A", "role_index": "1", "tail": "1111",
    }
    assert json.loads((users_dir / "内部A.session.json").read_text(
        encoding="utf-8"))["score"] == 7
    assert json.loads((users_dir / "内部A.json").read_text(
        encoding="utf-8"))["document_type"] == "lvjiang.user"

    migrated = json.loads(session_path.read_text(encoding="utf-8"))
    assert migrated["users"] == ["内部A", "角色B"]
    assert migrated["batch"]["configs"]["日常"]["usernames"] == ["内部A"]
    assert "rows" not in migrated["batch"]["configs"]["日常"]
    assert "enabled_rows" not in migrated["batch"]
    assert migrated["migrations"]["user_storage_v1"] is True
    assert (tmp_path / "session.pre-user_storage_v1.json").exists()

    # 再次加载不得覆盖用户后来维护的资料或 Session。
    metadata = json.loads((users_dir / "内部A.json").read_text(encoding="utf-8"))
    metadata["attributes"]["role"] = "后来修改"
    _write_json(users_dir / "内部A.json", metadata)
    _write_json(users_dir / "内部A.session.json", {"score": 9})
    reset_session_store()
    reloaded = UserConfigManager()
    assert reloaded.get_user("内部A").attributes["role"] == "后来修改"
    assert json.loads((users_dir / "内部A.session.json").read_text(
        encoding="utf-8"))["score"] == 9
    reset_session_store()


def test_role_column_legacy_config_uses_role_as_username(tmp_path, monkeypatch):
    from lvjiang import constants

    session_path = tmp_path / "session.json"
    monkeypatch.setattr(constants, "SESSION_PATH", session_path)
    monkeypatch.setattr(constants, "USERS_DIR", tmp_path / "users")
    _write_json(session_path, {
        "users": [],
        "batch": {"configs": {"旧配置": {
            "user_column": "role",
            "rows": [{"role": "蔡元君", "account": "账号", "role_index": "1"}],
            "workflows": {},
        }}},
    })
    reset_session_store()

    manager = UserConfigManager()
    assert manager.list_users() == ["蔡元君"]
    assert manager.get_user("蔡元君").attributes["role"] == "蔡元君"
    migrated = json.loads(session_path.read_text(encoding="utf-8"))
    assert migrated["batch"]["configs"]["旧配置"]["usernames"] == ["蔡元君"]
    reset_session_store()


def test_preloaded_second_store_observes_disk_marker_and_never_reruns(tmp_path):
    from lvjiang.core.config.session import SessionStore
    from lvjiang.core.config.user_storage_migration import migrate_user_storage

    session_path = tmp_path / "session.json"
    users_dir = tmp_path / "users"
    _write_json(session_path, {
        "users": [{"name": "alice"}],
        "batch": {"configs": {"daily": {
            "user_column": "role",
            "rows": [{"role": "alice", "account": "account-a"}],
            "workflows": {},
        }}},
    })
    first = SessionStore(session_path)
    stale_second = SessionStore(session_path)
    assert migrate_user_storage(first, users_dir) is True

    first.mutate_document(
        lambda data: data["batch"]["configs"]["daily"].update(
            {"usernames": ["alice", "later-user"]}
        )
    )

    assert migrate_user_storage(stale_second, users_dir) is False
    latest = json.loads(session_path.read_text(encoding="utf-8"))
    assert latest["batch"]["configs"]["daily"]["usernames"] == [
        "alice", "later-user",
    ]


def test_existing_marker_never_moves_user_files(tmp_path, monkeypatch):
    from lvjiang.core.config.session import SessionStore
    from lvjiang.core.config.user_storage_migration import migrate_user_storage

    session_path = tmp_path / "session.json"
    users_dir = tmp_path / "users"
    _write_json(session_path, {
        "users": ["alice"],
        "migrations": {"user_storage_v1": True},
    })
    source = users_dir / "alice.json"
    _write_json(source, {"current_user": "alice", "sentinel": "untouched"})
    monkeypatch.setattr(
        "lvjiang.core.access.is_readonly",
        lambda: True,
    )

    assert migrate_user_storage(SessionStore(session_path), users_dir) is False
    assert json.loads(source.read_text(encoding="utf-8"))["sentinel"] == "untouched"
    assert not (users_dir / "alice.session.json").exists()
