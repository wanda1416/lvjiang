"""Real process contention and readonly configuration persistence boundaries."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from lvjiang.core import access
from lvjiang.core.config.resolver import ConfigResolver
from lvjiang.core.config.session import SessionStore
from lvjiang.core.config.users import SessionManager


def child(code, root):
    env = dict(
        os.environ,
        LVJIANG_ROOT=str(root),
        PYTHONPATH=str(Path(__file__).resolve().parents[2] / "src"),
        PYTHONUTF8="1",
        PYTHONIOENCODING="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True,
        text=True, encoding="utf-8", timeout=20,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_instance_election_and_user_execution_are_independent(tmp_path):
    lease = access.Lease(tmp_path / "config/session/.locks/configuration.lock")
    assert lease.acquire()
    code = """
from lvjiang.constants import PROJECT_ROOT
from lvjiang.core.access import initialize_instance, is_readonly, close_instance
initialize_instance(PROJECT_ROOT)
print(is_readonly())
close_instance()
"""
    try:
        assert child(code, tmp_path) == "True"
    finally:
        lease.release()
    assert child(code, tmp_path) == "False"


def test_same_user_rejected_other_user_allowed_and_lock_file_can_remain(tmp_path):
    users = tmp_path / "config/session/users"
    lease = access.acquire_user("alice", users)
    code = """
from lvjiang.core.access import acquire_user, AccessDeniedError
try:
    a = acquire_user('alice')
except AccessDeniedError:
    print('busy')
else:
    print('free')
    a.release()
b = acquire_user('bob')
b.release()
"""
    try:
        with pytest.raises(access.AccessDeniedError):
            access.acquire_user("alice", users)
        assert child(code, tmp_path) == "busy"
        store = SessionManager(users)
        store.update("alice", lambda data: data.update(note="editable while running"))
        assert store.load("alice")["note"] == "editable while running"
    finally:
        lease.release()
    assert child(code, tmp_path) == "free"


def test_crashed_process_releases_user_lock(tmp_path):
    assert child("""
import os
from lvjiang.core.access import acquire_user
lease = acquire_user('alice')
os._exit(0)
""", tmp_path) == ""
    lease = access.acquire_user("alice", tmp_path / "config/session/users")
    lease.release()


def test_readonly_only_discards_whitelisted_session_paths(tmp_path, monkeypatch):
    session = SessionStore(tmp_path / "session.json")
    session.set_active("plan", "original")
    session.set_node("settings", {"env": "desktop", "language": "zh_CN"})
    session.set_node("daily", {"workflow_id": "old", "scripts": {"order": ["a"]}})
    session.set_node("profile", {
        "overview_active_group": "old",
        "overview_groups": {"old": {"columns": ["name"]}},
    })
    session.set_node("ui_state", {
        "main_page": {"left_tab_index": 0},
        "loadout_user:alice": {"equip_filter": {"type": "all"}},
    })
    resolver = ConfigResolver(tmp_path / "system", tmp_path / "local", dev_mode=True)
    resolver.save_merged("app.yaml", {"plans": [{"name": "original"}]})
    monkeypatch.setattr(access, "_readonly", True)

    session.set_active("plan", "temporary")
    session.update_node("settings", {"env": "android", "language": "en_US"})
    session.update_node("daily", {
        "workflow_id": "new", "scripts": {"order": ["b"]},
    })
    session.update_node("profile", {
        "overview_active_group": "new",
        "overview_groups": {"new": {"columns": ["progress"]}},
    })
    session.mutate_node("ui_state", lambda old: {
        **old,
        "main_page": {"left_tab_index": 1},
        "loadout_user:alice": {"equip_filter": {"type": "ring"}},
    })
    session.set_node("new_plugin", {"enabled": True})
    session.reload()

    assert session.get_active("plan") == "temporary"
    assert session.get_node("settings") == {"env": "android", "language": "en_US"}
    assert session.get_node("daily")["workflow_id"] == "new"
    assert session.get_node("profile")["overview_active_group"] == "new"
    assert session.get_node("ui_state")["main_page"]["left_tab_index"] == 1

    disk = json.loads((tmp_path / "session.json").read_text())
    assert disk["actives"]["plan"] == "original"
    assert disk["settings"] == {"env": "desktop", "language": "en_US"}
    assert disk["daily"] == {"workflow_id": "old", "scripts": {"order": ["b"]}}
    assert disk["profile"] == {
        "overview_active_group": "old",
        "overview_groups": {"new": {"columns": ["progress"]}},
    }
    assert disk["ui_state"] == {
        "main_page": {"left_tab_index": 0},
        "loadout_user:alice": {"equip_filter": {"type": "ring"}},
    }
    assert disk["new_plugin"] == {"enabled": True}

    resolver.save_merged("app.yaml", {"plans": [{"name": "temporary"}]})
    assert resolver.load_merged("app.yaml")["plans"][0]["name"] == "temporary"
    assert "temporary" in (tmp_path / "system/app.yaml").read_text()
    resolver.write_entity("scenes/new.yaml", "{}")
    assert resolver.resolve_read("scenes/new.yaml") is not None
    users = SessionManager(tmp_path / "users")
    data = users.load("alice")
    data["equipment"] = {"plan": "new"}
    users.save("alice", data)
    assert users.load("alice")["equipment"]["plan"] == "new"


def test_user_session_last_full_save_wins_across_processes(tmp_path):
    users = SessionManager(tmp_path / "config/session/users")
    snapshot = users.load("alice")
    child("""
from lvjiang.core.config.users import SessionManager
store = SessionManager()
data = store.load('alice')
data['other'] = 42
store.save('alice', data)
""", tmp_path)
    snapshot["mine"] = 7
    users.save("alice", snapshot)
    assert users.load("alice") == {"current_user": "alice", "mine": 7}


def test_readonly_history_write_does_not_flush_temporary_settings(tmp_path, monkeypatch):
    store = SessionStore(tmp_path / "session.json")
    store.set_node("profile", {"overview_active_group": "original"})
    monkeypatch.setattr(access, "_readonly", True)
    store.update_node("profile", {"overview_active_group": "temporary"})
    store.mutate_runtime_path("profile", "alert_history", lambda _: {"alice:key": "now"})
    assert store.get_node("profile")["overview_active_group"] == "temporary"
    disk = json.loads((tmp_path / "session.json").read_text())
    assert disk["profile"] == {
        "overview_active_group": "original", "alert_history": {"alice:key": "now"},
    }


def test_readonly_reload_refreshes_writable_state_and_keeps_transients(tmp_path, monkeypatch):
    path = tmp_path / "session.json"
    store = SessionStore(path)
    store.set_node("settings", {"env": "desktop", "language": "zh_CN"})
    monkeypatch.setattr(access, "_readonly", True)
    store.update_node("settings", {"env": "android"})

    disk = json.loads(path.read_text())
    disk["settings"]["language"] = "en_US"
    path.write_text(json.dumps(disk), encoding="utf-8")
    store.reload()

    assert store.get_node("settings") == {"env": "android", "language": "en_US"}


def test_readonly_user_loadout_stays_editable_during_execution(tmp_path, monkeypatch):
    from lvjiang.apps.yysls.core.loadout.repository import LoadoutRepository
    monkeypatch.setattr(access, "_readonly", True)
    repo = LoadoutRepository("alice", tmp_path)
    plan = repo.create_plan("new", "main", "sub")
    assert repo.load().plans[plan.id].name == "new"
    lease = access.acquire_user("alice", tmp_path)
    try:
        edited = repo.create_plan("ui", "main", "sub")
        assert edited.id in repo.load().plans
        with pytest.raises(access.AccessDeniedError):
            access.acquire_user("alice", tmp_path)
        with lease.authorized():
            created = repo.create_plan("workflow", "main", "sub")
        assert created.id in repo.load().plans
    finally:
        lease.release()


@pytest.mark.parametrize("readonly", [False, True])
def test_execution_does_not_lock_equipment_ui_or_profile(tmp_path, monkeypatch, readonly):
    from lvjiang.apps.yysls.core.loadout.repository import LoadoutRepository
    from lvjiang.core.profile.repository import ProfileDB

    monkeypatch.setattr(access, "_readonly", readonly)
    users_dir = tmp_path / "users"
    repo = LoadoutRepository("alice", users_dir)
    profile = ProfileDB(tmp_path / "profile.db")
    lease = access.acquire_user("alice", users_dir)
    try:
        initial = repo.load()
        assert not repo.path.exists()  # reading an empty account is pure
        assert repo.get_ui_state("equip_filter") == {}
        repo.set_ui_state("equip_filter", {"type": "ring", "sort": "level_desc"})
        assert repo.get_ui_state("equip_filter")["type"] == "ring"
        assert not repo.path.exists()
        other_reader = LoadoutRepository("alice", users_dir)
        assert other_reader.load().active_plan_id == initial.active_plan_id
        repo.assign_equipment(initial.active_plan_id, "ring", {"_fp": "ring", "type": "环"})
        snapshot = other_reader.load()
        repo.upsert_item({"_fp": "new", "type": "环"})
        assert "new" not in snapshot.equipment_items
        assert "new" in other_reader.load().equipment_items
        profile.upsert("alice", "daily", "progress", 3)
        with pytest.raises(access.AccessDeniedError):
            access.acquire_user("alice", users_dir)
    finally:
        lease.release()


def test_equipment_transactions_from_another_process_preserve_latest_items(tmp_path):
    from lvjiang.apps.yysls.core.loadout.repository import LoadoutRepository

    config_source = Path(__file__).resolve().parents[2] / "config/system/yysls/game_config.yaml"
    config_target = tmp_path / "config/system/yysls/game_config.yaml"
    config_target.parent.mkdir(parents=True)
    config_target.write_bytes(config_source.read_bytes())
    users = tmp_path / "config/session/users"
    repo = LoadoutRepository("alice", users)
    repo.upsert_item({"_fp": "first", "type": "环"})
    before_revision = repo.load().revision
    lease = access.acquire_user("alice", users)
    try:
        child("""
from lvjiang.apps.yysls.core.loadout.repository import LoadoutRepository
repo = LoadoutRepository('alice')
repo.upsert_item({'_fp': 'child', 'type': '环'})
repo.set_ui_state('equip_filter', {'type': 'ring'})
print(repo.get_ui_state('equip_filter')['type'])
""", tmp_path)
        repo.upsert_item({"_fp": "parent", "type": "环"})
    finally:
        lease.release()
    state = repo.load()
    assert set(state.equipment_items) == {"first", "child", "parent"}
    assert state.revision == before_revision + 2
