"""只读节点不能落盘配置；长快照不能覆盖新数据；安装刷新不持临界区。"""
import json
import threading
from contextlib import contextmanager

import pytest

from lvjiang.core import access
from lvjiang.core.batch_config import (
    BatchConfigConflict,
    BatchConfigItem,
    BatchConfigStore,
)
from lvjiang.core.config.content import configuration_lock
from lvjiang.core.config.document_store import DocumentDirectoryStore
from lvjiang.core.config.resolver import ConfigResolver
from lvjiang.core.config.update_store import UpdateStore
from lvjiang.core.config.users import SessionManager, UserSessionConflict
from lvjiang.core.update_bundle import tree_digest


def test_stale_user_session_rejects_same_field_and_preserves_both_unrelated_changes(tmp_path):
    manager = SessionManager(tmp_path / "users")
    manager.save("tester", {"count": 1, "old": 1})
    snapshot = manager.load("tester")
    manager.update("tester", lambda data: data.update(count=2, external=3))
    snapshot["count"] = 4
    with pytest.raises(UserSessionConflict):
        manager.save("tester", snapshot)
    assert manager.load("tester") == {"count": 2, "old": 1, "external": 3}
    snapshot["count"] = 2
    del snapshot["old"]
    manager.save("tester", snapshot)
    assert manager.load("tester") == {"count": 2, "external": 3}


def test_batch_draft_checks_baseline_and_explicit_override_updates_it(tmp_path):
    store = BatchConfigStore(tmp_path / "batch.json")
    draft = store.load()
    draft.add(BatchConfigItem(id="a", name="旧名称"))
    store.save(draft)
    draft.configs["a"].name = "草稿"
    store.mutate(lambda current: setattr(current.configs["a"], "name", "外部更新"))
    with pytest.raises(BatchConfigConflict):
        store.save(draft)
    assert store.load().configs["a"].name == "外部更新"
    store.save(draft, force=True)
    store.save(draft)
    assert store.load().configs["a"].name == "草稿"


def test_readonly_can_write_runtime_stores_but_not_installs(tmp_path, monkeypatch):
    """只读节点能改配置与场景文档，整目录替换仍限主节点。

    任务执行读的是启动时冻结的配置快照，运行中改配置只影响下一次任务，所以
    配置与场景文档不再要求主节点；更新包安装会整目录替换，仍必须独占。
    """
    resolver = ConfigResolver(config_dir=tmp_path, dev_mode=False)
    directory = DocumentDirectoryStore(tmp_path / "session/plugin", {"sample": "sample.json"})
    monkeypatch.setattr(access, "_readonly", True)

    assert directory.load("sample") == {}
    directory.mutate("sample", lambda _: {"value": 1})
    assert directory.load("sample") == {"value": 1}
    resolver.write_entity("scenes/sample.yaml", "key: sample\n")
    assert resolver.load_merged("scenes/sample.yaml") is not None

    system = tmp_path / "system"
    system.mkdir()
    (system / "opaque").write_bytes(b"old")
    store = UpdateStore(tmp_path, "0.15.0", tree_digest({"opaque": b"old"}))
    with pytest.raises(access.AccessDeniedError):
        store.install(member_files={"opaque": b"new"}, activate=lambda *_: None)
    assert (system / "opaque").read_bytes() == b"old"


def test_installed_callback_can_read_configuration_from_another_thread(tmp_path):
    system = tmp_path / "system"
    system.mkdir()
    (system / "opaque").write_bytes(b"old")
    store = UpdateStore(tmp_path, "0.15.0", tree_digest({"opaque": b"old"}))
    acquired = []

    def probe():
        with configuration_lock(tmp_path, blocking=False) as available:
            acquired.append(available)

    def refresh(*_args):
        worker = threading.Thread(target=probe)
        worker.start()
        worker.join(5)
        assert not worker.is_alive()

    store.install(member_files={"opaque": b"new"}, activate=refresh)
    assert acquired == [True]
    # 会员文件记录在 member 节点下；state 顶层只保留语义字段（files 已随修订号加载重构移除）。
    assert json.loads(store.state_path.read_text(encoding="utf-8"))["member"]["files"]
    assert not (tmp_path / "session/config.lock").exists()


@contextmanager
def _occupied_user_lease(users_dir_path, username: str):
    """模拟另一个进程持有该用户的执行租约。

    直接占住锁文件而不经过 ``Lease``，这样本进程的 ``_held`` 缓存不会命中，
    等价于跨进程占用。
    """
    from fasteners import InterProcessLock

    lease = access.user_lease(username, users_dir_path)
    with InterProcessLock(lease.key):
        yield


def test_ui_state_write_is_not_blocked_by_user_execution_lease(tmp_path):
    """界面偏好与任务数据无关：别的进程在跑任务时也必须写得进去。

    历史崩溃：点击装备槽位保存筛选走 user_write_access，被对方进程的租约
    拒绝后直接冒到 Qt 事件循环。
    """
    from lvjiang.core import user_config

    users_dir = tmp_path / "users"
    users_dir.mkdir()
    user_config.save_user_metadata(
        user_config.User(name="测试用户", created_at=""), users_dir)

    with _occupied_user_lease(users_dir, "测试用户"):
        user_config.set_user_ui_state(
            "测试用户", "equip_filter", {"sort": "level"}, users_dir)
        assert user_config.get_user_ui_state(
            "测试用户", "equip_filter", users_dir) == {"sort": "level"}


def test_user_metadata_write_still_respects_the_execution_lease(tmp_path):
    """任务执行期间仍然不允许别的进程改用户资料，界面偏好是唯一的例外。"""
    from lvjiang.core import user_config

    users_dir = tmp_path / "users"
    users_dir.mkdir()
    user_config.save_user_metadata(
        user_config.User(name="测试用户", created_at=""), users_dir)

    with _occupied_user_lease(users_dir, "测试用户"):
        with pytest.raises(access.AccessDeniedError):
            user_config.set_user_workflow_params(
                "测试用户", "wf", {"n": 1}, users_dir)
