"""备战方案仓库遵守宿主的实例/用户占用门禁（真实子进程争用）。

宿主的占用机制本身由 ``tests/core/test_instance_access.py`` 覆盖；这里验证
yysls 的 ``LoadoutRepository`` 写入路径确实走了占用授权。
"""
import shutil
from pathlib import Path

import pytest

from lvjiang.core import access
from tests.core.test_instance_access import child


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
    from lvjiang.core.users.document import User, save_user_metadata

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
    from lvjiang.core.users.document import User, save_user_metadata
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
