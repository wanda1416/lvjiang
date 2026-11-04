"""保护全用户重命名、历史来源以及失败后的幂等重放契约。"""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from lvjiang import constants
from lvjiang.core.config.interface import interface_path
from lvjiang.core.profile import key_rename, repository, schema
from lvjiang.core.profile.models import QuotaKeyDef, StockKeyDef, SyncTargetDef
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


def test_rename_emits_baselines_for_all_users(tmp_path):
    """改名后自动重发全量基线 set：跨端无感传播的核心契约。

    其他设备（含手机）对新 key 没有基线，第一条 delta 会在 0 上算错；
    每用户一行 set(当前值) 随分钟级自动同步上行，回放端按时间戳合并直接
    落正确值。锁定条目与空占位不发。
    """
    db = ProfileDB(tmp_path / "profile.db")
    for user in ("tester_a", "tester_b"):
        db.upsert(user, "quota", "weekly_old", 5, change_type="delta", delta_value=5)
    db.upsert("tester_c", "quota", "weekly_old", 0)          # 空占位用户
    db.set_locked("tester_b", "quota", "weekly_old")          # 锁定用户
    schema.save_profile_config(schema.ProfileSchema(keys_by_model={"quota": [
        QuotaKeyDef(key="weekly_new", label="周"),
    ]}))
    schema.reload_profile_config()

    # 真实流程：先原子改名（emit 读取的是新 key），再重发基线
    db.rename_keys([("quota", "weekly_old", "weekly_new")],
                   save_references=lambda: None)
    key_rename._emit_rename_baselines(db, [("quota", "weekly_old", "weekly_new")])

    # 只有 tester_a 的非零值获得基线行；锁定与空占位不发
    rows = db.get_history("tester_a", "quota", "weekly_new")
    baseline = [r for r in rows if r["change_type"] == "set"]
    assert len(baseline) == 1
    assert baseline[0]["new_value"] == 5.0
    assert baseline[0]["source"] == "key 改名重设"
    # 锁定用户：旧历史行随改名重写（审计保留），但**不发新基线 set**
    locked_rows = db.get_history("tester_b", "quota", "weekly_new")
    assert locked_rows and all(r["change_type"] != "set" for r in locked_rows)
    # 空占位用户同样不发
    assert not db.get_history("tester_c", "quota", "weekly_new")
    # 基线行的 id 在改名前的行之上：随水位推进自然上行
    older = [r for r in rows if r["change_type"] == "delta"]
    assert all(baseline[0]["id"] > r["id"] for r in older)


def _write_journal(db, renames) -> None:
    from lvjiang.core.fs_util import atomic_write_text
    from lvjiang.core.json_files import formatted_json

    atomic_write_text(
        key_rename._journal_path(db),
        formatted_json({"renames": [list(item) for item in renames]}),
        prefix=".profile_rename_",
    )


def test_recover_renames_definition_from_disk_before_files_were_written():
    """日志还在、文件尚未改写时，重放同时把定义 key 和数据库改到新名。"""
    old = schema.ProfileSchema(keys_by_model={"stock": [
        StockKeyDef(key="credits", label="资源"),
    ]})
    schema.save_profile_config(old)
    schema.reload_profile_config()
    db = repository.get_profile_db()
    db.upsert("tester", "stock", "credits", 4, change_type="delta", delta_value=4)
    _write_journal(db, [("stock", "credits", "coins")])

    key_rename.recover_rename(db)

    assert not key_rename._journal_path(db).exists()
    assert schema.get_profile_config().get_key("coins").label == "资源"
    assert schema.get_profile_config().get_key("credits") is None
    assert "coins" in schema._PROFILE_PATH.read_text(encoding="utf-8")
    assert db.get_entry("tester", "stock", "coins")["value"] == 4
    assert not db.get_entry("tester", "stock", "credits")


def test_recover_rewrites_stale_yaml_when_database_already_has_new_key():
    """数据库已改名、磁盘和内存单例仍是旧 key 时，重放把定义写成新 key，不搬回数据。"""
    old = schema.ProfileSchema(keys_by_model={"stock": [
        StockKeyDef(key="credits", label="资源",
                    sync_targets=[SyncTargetDef(key="stock:credits")]),
    ]})
    schema.save_profile_config(old)
    schema.reload_profile_config()
    db = repository.get_profile_db()
    db.upsert("tester", "stock", "credits", 4, change_type="delta", delta_value=4)
    db.rename_keys([("stock", "credits", "coins")], save_references=lambda: None)
    schema.reload_profile_config()
    assert schema.get_profile_config().get_key("credits") is not None
    _write_journal(db, [("stock", "credits", "coins")])

    key_rename.recover_rename(db)

    assert schema.get_profile_config().get_key("coins").label == "资源"
    assert schema.get_profile_config().get_key("coins").sync_targets[0].key == "stock:coins"
    assert schema.get_profile_config().get_key("credits") is None
    assert db.get_entry("tester", "stock", "coins")["value"] == 4
    assert not db.get_history("tester", "stock", "credits")

    _write_journal(db, [("stock", "credits", "coins")])
    key_rename.recover_rename(db)
    saved = schema._PROFILE_PATH.read_text(encoding="utf-8")
    assert saved.count("key: coins") == 1
    assert "key: credits" not in saved
    assert db.get_entry("tester", "stock", "coins")["value"] == 4


def test_save_renamed_definitions_publishes_new_key_and_one_baseline_round():
    old = schema.ProfileSchema(keys_by_model={"quota": [
        QuotaKeyDef(key="weekly_old", label="周"),
    ]})
    schema.save_profile_config(old)
    schema.reload_profile_config()
    db = repository.get_profile_db()
    db.upsert("tester", "quota", "weekly_old", 5, change_type="delta", delta_value=5)
    updated = deepcopy(old)
    updated.keys_by_model["quota"][0].key = "weekly_new"
    updated._rebuild_index()

    key_rename.save_renamed_definitions(
        updated, [("quota", "weekly_old", "weekly_new")], db=db)

    assert schema.get_profile_config().get_key("weekly_new").label == "周"
    assert schema.get_profile_config().get_key("weekly_old") is None
    disk = schema._PROFILE_PATH.read_text(encoding="utf-8")
    assert "weekly_new" in disk
    assert "weekly_old" not in disk
    sets = [
        row for row in db.get_history("tester", "quota", "weekly_new")
        if row["change_type"] == "set" and row["source"] == "key 改名重设"
    ]
    assert len(sets) == 1
    assert sets[0]["new_value"] == 5.0
    assert not db.get_history("tester", "quota", "weekly_old")


def test_emit_skips_baseline_when_definition_is_still_old_key(tmp_path):
    db = ProfileDB(tmp_path / "emit-skip.db")
    db.upsert("tester", "quota", "weekly_old", 5, change_type="delta", delta_value=5)
    db.rename_keys([("quota", "weekly_old", "weekly_new")], save_references=lambda: None)
    schema.save_profile_config(schema.ProfileSchema(keys_by_model={"quota": [
        QuotaKeyDef(key="weekly_old", label="周"),
    ]}))
    schema.reload_profile_config()

    key_rename._emit_rename_baselines(db, [("quota", "weekly_old", "weekly_new")])

    assert not [
        row for row in db.get_history("tester", "quota", "weekly_new")
        if row["change_type"] == "set"
    ]
