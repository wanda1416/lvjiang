"""只读节点不能落盘配置；长快照不能覆盖新数据；安装刷新不持临界区。"""
import json
import threading

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


def test_readonly_does_not_initialize_or_write_shared_stores(tmp_path, monkeypatch):
    resolver = ConfigResolver(config_dir=tmp_path, dev_mode=False)
    directory = DocumentDirectoryStore(tmp_path / "session/plugin", {"sample": "sample.json"})
    monkeypatch.setattr(access, "_readonly", True)
    assert directory.load("sample") == {}
    assert not directory.root.exists()
    with pytest.raises(access.AccessDeniedError):
        directory.mutate("sample", lambda _: {"value": 1})
    with pytest.raises(access.AccessDeniedError):
        resolver.write_entity("scenes/sample.yaml", "key: sample\n")
    system = tmp_path / "system"
    system.mkdir()
    (system / "opaque").write_bytes(b"old")
    store = UpdateStore(tmp_path, "0.15.0", tree_digest({"opaque": b"old"}))
    with pytest.raises(access.AccessDeniedError):
        store.install(member_files={"opaque": b"new"}, activate=lambda *_: None)
    assert (system / "opaque").read_bytes() == b"old"
    assert not list(tmp_path.rglob("*.lock"))


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
    assert json.loads(store.state_path.read_text(encoding="utf-8"))["files"]
    assert not (tmp_path / "session/config.lock").exists()
