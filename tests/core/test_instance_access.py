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


def test_instance_election_and_user_execution_are_independent(tmp_path, monkeypatch):
    # 锁统一落在 ``config/session/.locks``：父进程要和子进程（LVJIANG_ROOT=tmp_path）
    # 用同一个 session 目录，才能撞在同一把锁上。
    from lvjiang import constants
    from lvjiang.core.fs_util import lock_file
    monkeypatch.setattr(constants, "SESSION_CONFIG_DIR", tmp_path / "config/session")
    session = (tmp_path / "config/session").resolve()
    lease = access.Lease(lock_file("instance", os.path.normcase(str(session))))
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


def test_declared_readonly_skips_election(tmp_path, monkeypatch):
    """``--read-only`` 不参与抢占：自己不占锁，主节点留给真正要干活的实例。"""
    from lvjiang import constants
    monkeypatch.setattr(constants, "SESSION_CONFIG_DIR", tmp_path / "config/session")
    code = """
import os
from lvjiang.constants import PROJECT_ROOT
from lvjiang.core.access import initialize_instance, is_readonly, Lease
from lvjiang.core.fs_util import lock_file
from lvjiang.core.paths import config_directory, session_directory
initialize_instance(PROJECT_ROOT, readonly=True)
print(is_readonly())
key = os.path.normcase(str(session_directory(config_directory(PROJECT_ROOT)).resolve()))
print(Lease(lock_file('instance', key)).acquire())
"""
    assert child(code, tmp_path).splitlines() == ["True", "True"]


def test_same_user_rejected_other_user_allowed_and_lock_file_can_remain(tmp_path, monkeypatch):
    from lvjiang import constants
    monkeypatch.setattr(constants, "SESSION_CONFIG_DIR", tmp_path / "config/session")
    users = tmp_path / "config/session/users"
    lease = access.acquire_user("alice", users)
    assert Path(lease.key).parent == (tmp_path / "config/session/.locks").resolve()
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


def test_readonly_instance_writes_persist_like_primary(tmp_path, monkeypatch):
    """只读实例的所有界面/配置写入都落盘；唯一保留的限制是全局热键与 MCP。"""
    session = SessionStore(tmp_path / "session.json")
    interface = InterfaceStore(tmp_path / "interface.json")
    resolver = ConfigResolver(tmp_path / "system", tmp_path / "local", dev_mode=True)
    resolver.save_merged("app.yaml", {"plans": [{"name": "original"}]})
    monkeypatch.setattr(access, "_readonly", True)

    interface.set_active("plan", "temporary")
    interface.set_node("daily", {"workflow_id": "b", "group": "周常"})
    interface.mutate_node("ui_state", lambda old: {
        **(old or {}), "main_page": {"left_tab_index": 1}})
    session.set_node("settings", {"language": "en_US"})
    resolver.save_merged("app.yaml", {"plans": [{"name": "temporary"}]})

    interface.reload()
    assert interface.get_active("plan") == "temporary"
    assert interface.get_node("daily") == {"workflow_id": "b", "group": "周常"}
    assert interface.get_node("ui_state")["main_page"]["left_tab_index"] == 1
    disk = json.loads((tmp_path / "session.json").read_text(encoding="utf-8"))
    assert disk["settings"] == {"language": "en_US"}
    assert resolver.load_merged("app.yaml")["plans"][0]["name"] == "temporary"
    resolver.write_entity("scenes/new.yaml", "{}")


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


def test_readonly_alert_and_history_writes_persist(tmp_path, monkeypatch):
    """告警与去重标记属于运行时状态，任何实例都必须写得进去并落盘。"""
    store = InterfaceStore(tmp_path / "interface.json")
    monkeypatch.setattr(access, "_readonly", True)
    store.update_node("profile", {"overview_active_group": "temporary"})
    store.mutate_node("alert_history", lambda _: {"alice:key": "now"})
    disk = json.loads((tmp_path / "interface.json").read_text(encoding="utf-8"))
    assert disk["profile"]["overview_active_group"] == "temporary"
    assert disk["alert_history"] == {"alice:key": "now"}


def test_alert_writes_converge_on_any_instance(tmp_path, monkeypatch):
    """ProfileEngine 的告警写入在任何实例下都不能失败或反复重试。"""
    from lvjiang.core.config import interface as interface_module
    from lvjiang.core.profile import store as profile_store

    monkeypatch.setattr(
        interface_module, "_store",
        interface_module.InterfaceStore(tmp_path / "interface.json"))
    monkeypatch.setattr(access, "_readonly", True)

    assert interface_module.add_alert(
        "profile:alice:tili:red", "体力偏低", "2026-10-23T13:43:47")
    profile_store.mark_alert(
        "alice:tili:red:current", "2026-10-23T13:43:47")

    disk = json.loads(
        (tmp_path / "interface.json").read_text(encoding="utf-8"))
    assert [item["id"] for item in disk["alert_info"]] == [
        "profile:alice:tili:red"]
    assert disk["alert_history"] == {
        "alice:tili:red:current": "2026-10-23T13:43:47"}


def test_two_stores_merge_each_others_nodes(tmp_path):
    """两个实例各写自己的节点：后写者必须基于最新磁盘合并，不能覆盖前者。"""
    first = InterfaceStore(tmp_path / "interface.json")
    second = InterfaceStore(tmp_path / "interface.json")

    first.set_node("alert_history", {"a": "1"})
    second.set_node("server_config", {"skip_version": "9.9.9"})

    disk = json.loads(
        (tmp_path / "interface.json").read_text(encoding="utf-8"))
    assert disk["alert_history"] == {"a": "1"}
    assert disk["server_config"] == {"skip_version": "9.9.9"}


def test_readonly_reload_refreshes_from_disk(tmp_path, monkeypatch):
    path = tmp_path / "interface.json"
    store = InterfaceStore(path)
    store.set_node("server_config", {"skip_version": "1.0.0"})
    monkeypatch.setattr(access, "_readonly", True)
    store.update_node("server_config", {"skip_version": "2.0.0"})

    disk = json.loads(path.read_text(encoding="utf-8"))
    disk["server_config"]["skip_version"] = "3.0.0"   # 别的实例又写了一次
    path.write_text(json.dumps(disk), encoding="utf-8")
    store.reload()

    assert store.get_node("server_config")["skip_version"] == "3.0.0"


def test_readonly_instance_persists_batch_drafts_and_active_group(
    tmp_path, monkeypatch,
):
    """只读实例调整执行草稿与活动组后，重启照常恢复。"""
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

    assert batch_run.active_group_id() == "readonly"
    disk = json.loads(path.read_text(encoding="utf-8"))
    assert disk["batch"]["active_group_id"] == "readonly"
    assert disk["batch"]["drafts"]["gid"]["rounds"] == 9


def test_user_loadout_write_requires_free_or_authorized_user(tmp_path, monkeypatch):
    """未占用的用户可写（短暂取得占用）；被占用时必须持授权上下文。"""
    from lvjiang.apps.yysls.core.loadout.repository import LoadoutRepository
    monkeypatch.setattr(access, "_readonly", True)
    repo = LoadoutRepository("alice", tmp_path)
    created = repo.create_plan("free", "main", "sub")
    assert created.id in repo.load().plans
    lease = access.acquire_user("alice", tmp_path)
    try:
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


def test_equipment_write_from_another_process_is_rejected_while_user_is_occupied(
        tmp_path, monkeypatch):
    from lvjiang import constants
    from lvjiang.apps.yysls.core.loadout.repository import LoadoutRepository

    # 父进程与子进程（LVJIANG_ROOT=tmp_path）必须共用同一个锁根，占用才可见。
    monkeypatch.setattr(constants, "SESSION_CONFIG_DIR", tmp_path / "config/session")

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
