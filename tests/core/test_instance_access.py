"""Real process contention and readonly configuration persistence boundaries."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lvjiang.core import access
from lvjiang.core.config.interface import InterfaceStore
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
    assert Path(lease.key).parent == (users / ".lock").resolve()
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
    session.set_node("settings", {"language": "zh_CN"})
    session.set_node("daily", {"scripts": {"order": ["a"]}})
    interface = InterfaceStore(tmp_path / "interface.json")
    interface.set_active("plan", "original")
    interface.set_node("daily", {"workflow_id": "a", "group": None})
    interface.set_node("profile", {
        "overview_active_group": "old",
        "overview_groups": {"old": {"columns": ["name"]}},
    })
    interface.set_node("ui_state", {
        "main_page": {"left_tab_index": 0},
        "loadout_user:alice": {"equip_filter": {"type": "all"}},
    })
    resolver = ConfigResolver(tmp_path / "system", tmp_path / "local", dev_mode=True)
    resolver.save_merged("app.yaml", {"plans": [{"name": "original"}]})
    monkeypatch.setattr(access, "_readonly", True)

    # 白名单里的选择只在内存生效：激活项与界面状态都不落盘
    interface.set_active("plan", "temporary")
    interface.set_active("env", "android")
    interface.update_node("daily", {"workflow_id": "b", "group": "周常"})
    interface.update_node("profile", {
        "overview_active_group": "new",
    })
    interface.mutate_node("ui_state", lambda old: {
        **old,
        "main_page": {"left_tab_index": 1},
        "loadout_user:alice": {"equip_filter": {"type": "ring"}},
    })
    # 白名单外属于主节点配置，不能从只读节点落盘。
    with pytest.raises(access.AccessDeniedError):
        session.set_node("settings", {"language": "en_US"})
    with pytest.raises(access.AccessDeniedError):
        session.set_node("new_plugin", {"enabled": True})
    with pytest.raises(access.AccessDeniedError):
        interface.update_node("server_config", {"skip_version": "9.9.9"})
    session.reload()
    interface.reload()

    assert interface.get_active("plan") == "temporary"
    assert interface.get_active("env") == "android"
    assert interface.get_node("daily") == {"workflow_id": "b", "group": "周常"}
    assert interface.get_node("profile")["overview_active_group"] == "new"
    assert interface.get_node("ui_state")["main_page"]["left_tab_index"] == 1

    disk = json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))
    assert disk["settings"] == {"language": "zh_CN"}
    assert "new_plugin" not in disk
    interface_disk = json.loads((tmp_path / "interface.json").read_text(encoding="utf-8"))
    assert interface_disk["actives"] == {"plan": "original"}
    assert interface_disk["daily"] == {"workflow_id": "a", "group": None}
    assert interface_disk["profile"] == {
        "overview_active_group": "old",
        "overview_groups": {"old": {"columns": ["name"]}},
    }
    assert interface_disk["ui_state"] == {
        "main_page": {"left_tab_index": 0},
        "loadout_user:alice": {"equip_filter": {"type": "all"}},
    }
    assert interface_disk["server_config"] == {}

    with pytest.raises(access.AccessDeniedError):
        resolver.save_merged("app.yaml", {"plans": [{"name": "temporary"}]})
    assert resolver.load_merged("app.yaml")["plans"][0]["name"] == "original"
    with pytest.raises(access.AccessDeniedError):
        resolver.write_entity("scenes/new.yaml", "{}")
    users = SessionManager(tmp_path / "users")
    data = users.load("alice")
    data["equipment"] = {"plan": "new"}
    with pytest.raises(access.AccessDeniedError):
        users.save("alice", data)


def test_user_session_snapshot_preserves_unmodified_disk_fields(tmp_path):
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
    assert users.load("alice") == {"current_user": "alice", "mine": 7, "other": 42}


def test_readonly_history_write_does_not_flush_temporary_settings(tmp_path, monkeypatch):
    store = InterfaceStore(tmp_path / "interface.json")
    store.set_node("profile", {"overview_active_group": "original"})
    monkeypatch.setattr(access, "_readonly", True)
    store.update_node("profile", {"overview_active_group": "temporary"})
    with pytest.raises(access.AccessDeniedError):
        store.mutate_node("alert_history", lambda _: {"alice:key": "now"})
    assert store.get_node("profile")["overview_active_group"] == "temporary"
    disk = json.loads((tmp_path / "interface.json").read_text(encoding="utf-8"))
    assert disk["profile"] == {
        "overview_active_group": "original",
    }

    assert disk["alert_history"] == {}

def test_readonly_reload_refreshes_writable_state_and_keeps_transients(tmp_path, monkeypatch):
    path = tmp_path / "interface.json"
    store = InterfaceStore(path)
    store.set_node("actives", {"env": "desktop"})
    store.set_node("server_config", {"skip_version": "1.0.0"})
    monkeypatch.setattr(access, "_readonly", True)
    store.set_active("env", "android")                              # 白名单：只留内存
    with pytest.raises(access.AccessDeniedError):
        store.update_node("server_config", {"skip_version": "2.0.0"})

    disk = json.loads(path.read_text(encoding="utf-8"))
    disk["server_config"]["skip_version"] = "3.0.0"   # 别的实例又写了一次
    path.write_text(json.dumps(disk), encoding="utf-8")
    store.reload()

    # 本实例的临时选择保留，磁盘上别人的改动照常刷新进来
    assert store.get_active("env") == "android"
    assert store.get_node("server_config")["skip_version"] == "3.0.0"


def test_readonly_instance_keeps_batch_drafts_and_active_group_only_in_memory(
    tmp_path, monkeypatch,
):
    """只读节点可以调整执行草稿，但不能改变主节点保存的界面状态。"""
    from lvjiang.core import batch_run
    from lvjiang.core.batch_run import BatchRunDraft, BatchSelection

    path = tmp_path / "interface.json"
    store = InterfaceStore(path)
    monkeypatch.setattr(
        "lvjiang.core.config.interface.get_interface_store", lambda: store)
    store.set_node("batch", {"active_group_id": "primary", "drafts": {}})

    monkeypatch.setattr(access, "_readonly", True)
    batch_run.set_active_group_id("readonly")
    batch_run.save_draft("gid", BatchRunDraft(
        tasks=BatchSelection.from_dict({"keys": ["a"], "all": False}),
        users=BatchSelection.from_dict({"keys": ["u"], "all": False}),
        rounds=9, unattended=True))

    # 活动组不落盘，磁盘上仍是主实例的值。
    assert batch_run.active_group_id() == "readonly"
    disk = json.loads(path.read_text(encoding="utf-8"))
    assert disk["batch"]["active_group_id"] == "primary"
    # 只读节点的运行草稿只在内存生效，不能改主节点的界面记录。
    assert store.get_node("batch")["drafts"]["gid"]["rounds"] == 9
    assert disk["batch"]["drafts"] == {}


def test_readonly_user_loadout_stays_editable_during_execution(tmp_path, monkeypatch):
    from lvjiang.apps.yysls.core.loadout.repository import LoadoutRepository
    monkeypatch.setattr(access, "_readonly", True)
    repo = LoadoutRepository("alice", tmp_path)
    with pytest.raises(access.AccessDeniedError):
        repo.create_plan("new", "main", "sub")
    lease = access.acquire_user("alice", tmp_path)
    try:
        with pytest.raises(access.AccessDeniedError):
            repo.create_plan("ui", "main", "sub")
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
    from lvjiang.core.user_config import User, save_user_metadata

    users_dir = tmp_path / "users"
    save_user_metadata(User("alice"), users_dir)
    monkeypatch.setattr(access, "_readonly", readonly)
    repo = LoadoutRepository("alice", users_dir)
    profile = ProfileDB(tmp_path / "profile.db")
    lease = access.acquire_user("alice", users_dir)
    try:
        with lease.authorized():
            _exercise_equipment_writes(repo, profile, users_dir)
    finally:
        lease.release()


def _exercise_equipment_writes(repo, profile, users_dir):
    from lvjiang.apps.yysls.core.loadout.repository import LoadoutRepository
    initial = repo.load()
    assert not repo.path.exists()
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


def test_equipment_transactions_from_another_process_preserve_latest_items(tmp_path):
    from lvjiang.apps.yysls.core.loadout.repository import LoadoutRepository

    config_source = Path(__file__).resolve().parents[2] / "config/system/yysls/game_config"
    config_target = tmp_path / "config/system/yysls/game_config"
    shutil.copytree(config_source, config_target)
    users = tmp_path / "config/session/users"
    from lvjiang.core.user_config import User, save_user_metadata
    save_user_metadata(User("alice"), users)
    repo = LoadoutRepository("alice", users)
    repo.upsert_item({"_fp": "first", "type": "环"})
    before_revision = repo.load().revision
    lease = access.acquire_user("alice", users)
    try:
        child("""
from lvjiang.apps.yysls.core.loadout.repository import LoadoutRepository
repo = LoadoutRepository('alice')
from lvjiang.core.access import AccessDeniedError
try:
    repo.upsert_item({'_fp': 'child', 'type': '环'})
except AccessDeniedError:
    print('occupied')
else:
    raise AssertionError('不能越过其它节点的用户占用')
""", tmp_path)
        repo.upsert_item({"_fp": "parent", "type": "环"})
    finally:
        lease.release()
    state = repo.load()
    assert set(state.equipment_items) == {"first", "parent"}
    assert state.revision == before_revision + 1
