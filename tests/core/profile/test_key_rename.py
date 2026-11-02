"""保护全用户重命名、历史来源以及失败后的幂等重放契约。"""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from lvjiang import constants
from lvjiang.core.config.interface import interface_path
from lvjiang.core.profile import key_rename, repository, schema
from lvjiang.core.profile.models import StockKeyDef, SyncTargetDef
from lvjiang.core.profile.repository import ProfileDB


def test_rename_updates_all_users_refs_without_changing_values(tmp_path):
    db = ProfileDB(tmp_path / "profile.db")
    for user in ("tester_a", "tester_b"):
        db.upsert(user, "stock", "credits", 10, change_type="delta", delta_value=10)
        db.upsert(user, "stock", "reward", 1, change_type="delta", delta_value=1,
                  sync_from="stock:credits")
    original = db.get_entry("tester_a", "stock", "credits")
    db.rename_keys([("stock", "credits", "coins")], save_references=lambda: None)
    assert db.get_entry("tester_a", "stock", "coins") == original
    assert not db.get_entry("tester_b", "stock", "credits")
    assert db.get_history("tester_b", "stock", "reward")[0]["sync_from"] == "stock:coins"
    # 幂等：重放同一次改名不报错、不改变数据（崩溃恢复依赖这一点）。
    db.rename_keys([("stock", "credits", "coins")], save_references=lambda: None)
    assert db.get_entry("tester_a", "stock", "coins") == original
    with pytest.raises(ValueError, match="已有数据"):
        db.rename_keys([("stock", "coins", "reward")], save_references=lambda: None)
    assert db.get_entry("tester_a", "stock", "coins") == original


def test_rename_failure_keeps_journal_and_restart_converges(tmp_path, monkeypatch):
    interface_path().parent.mkdir(parents=True, exist_ok=True)
    old = schema.ProfileSchema(keys_by_model={"stock": [
        StockKeyDef(key="credits", label="资源"),
        StockKeyDef(key="reward", label="奖励", sync_targets=[SyncTargetDef(key="stock:credits")]),
    ]})
    schema.save_profile_config(old)
    schema.reload_profile_config()
    interface_path().write_text(json.dumps({"profile": {
        "overview_groups": {"group": {"columns": ["stock:credits"]}}}}))
    constants.BATCH_CONFIG_PATH.write_text(json.dumps({"groups": {"group": {"profile_sort_key": "credits"}}}))
    paths = (schema._PROFILE_PATH, interface_path(), constants.BATCH_CONFIG_PATH)
    before = {path: path.read_bytes() for path in paths}
    db = repository.get_profile_db()
    db.upsert("tester", "stock", "credits", 12)
    updated = deepcopy(old)
    updated.keys_by_model["stock"][0].key = "coins"

    from lvjiang.core.profile import triggers
    with monkeypatch.context() as patch:
        patch.setattr(triggers, "_runner", SimpleNamespace(is_busy=True))
        with pytest.raises(ValueError, match="队列"):
            key_rename.save_renamed_definitions(updated, [("stock", "credits", "coins")], db=db)
    assert all(path.read_bytes() == before[path] for path in paths)
    assert not key_rename._journal_path(db).exists()

    original_write = key_rename.atomic_write_text

    def fail_batch(path, *args, **kwargs):
        if path == constants.BATCH_CONFIG_PATH:
            raise OSError("test write failure")
        return original_write(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(key_rename, "atomic_write_text", fail_batch)
        with pytest.raises(OSError, match="test write failure"):
            key_rename.save_renamed_definitions(updated, [("stock", "credits", "coins")], db=db)

    # 中途失败：事务回滚，journal 保留（不删），DB 仍是旧 key。
    journal_path = key_rename._journal_path(db)
    assert journal_path.exists()
    assert db.get_entry("tester", "stock", "credits")["value"] == 12

    # 重启后按 journal 幂等重放：SQL 与引用文件都收敛到改名后的状态。
    ProfileDB(db._db_path)
    assert not journal_path.exists()
    assert db.get_entry("tester", "stock", "coins")["value"] == 12
    assert schema.get_profile_config().get_key("reward").sync_targets[0].key == "stock:coins"
    assert json.loads(interface_path().read_text())["profile"]["overview_groups"]["group"]["columns"] == ["stock:coins"]
    assert json.loads(constants.BATCH_CONFIG_PATH.read_text())["groups"]["group"]["profile_sort_key"] == "coins"
