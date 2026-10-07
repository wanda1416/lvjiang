"""保护全用户重命名、历史来源以及文件/SQL 失败隔离契约。"""

import base64
import json
import sqlite3
from copy import deepcopy
from types import SimpleNamespace

import pytest

from lvjiang import constants
from lvjiang.core.config.interface import interface_path
from lvjiang.core.profile import key_rename, repository, schema
from lvjiang.core.profile.models import StockKeyDef, SyncTargetDef
from lvjiang.core.profile.repository import MIGRATIONS, ProfileDB


def test_v7_preserves_history_and_separates_reset_sync_and_unknown(tmp_path):
    path = tmp_path / "profile.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
        for version, _, migrate in MIGRATIONS[:6]:
            migrate(conn)
            conn.execute("INSERT INTO schema_version VALUES (?)", (version,))
        rows = [
            ("quota", "progress", 8, 0, "tick", "reset:0"),
            ("regen", "energy", 5, 6, "tick", "regen:+1.2500"),
            ("stock", "reward", 1, 3, "action", "sync_from:progress"),
            ("regen", "energy", 6, 9, "action", "sync_from:progress"),
            ("stock", "reward", 3, 4, "action", "unrecognized"),
        ]
        conn.executemany(
            "INSERT INTO profile_history (ts,username,type,key,old_value,new_value,change_type,detail) "
            "VALUES ('2026-01-01','tester',?,?,?,?,?,?)", rows)
    db = ProfileDB(path)
    history = {row["id"]: row for row in db.get_history("tester")}
    assert history[1]["change_type"] == "reset" and history[1]["delta_value"] == -8
    assert history[2]["delta_value"] == 1.25
    assert history[3]["sync_from"] == "quota:progress" and history[3]["delta_value"] == 2
    assert history[4]["delta_value"] is None
    with db._connect() as conn:
        assert conn.execute("SELECT detail FROM profile_history_legacy WHERE history_id=5").fetchone()[0] == "unrecognized"
        assert "detail" not in {row[1] for row in conn.execute("PRAGMA table_info(profile_history)")}
    assert path.with_suffix(".before-v7.db").exists()
    assert ProfileDB(path).get_history("tester") == db.get_history("tester")


def test_rename_updates_all_users_refs_and_audit_without_changing_values(tmp_path):
    db = ProfileDB(tmp_path / "profile.db")
    for user in ("tester_a", "tester_b"):
        db.upsert(user, "stock", "credits", 10, change_type="action", delta_value=10)
        db.upsert(user, "stock", "reward", 1, change_type="action", delta_value=1,
                  sync_from="stock:credits")
    original = db.get_entry("tester_a", "stock", "credits")
    db.rename_keys([("stock", "credits", "coins")], operation_id="test",
                   save_references=lambda: None)
    assert db.get_entry("tester_a", "stock", "coins") == original
    assert not db.get_entry("tester_b", "stock", "credits")
    assert db.get_history("tester_b", "stock", "reward")[0]["sync_from"] == "stock:coins"
    audit = db.get_key_renames("stock", "coins")[0]
    assert (audit["entries_count"], audit["history_count"], audit["sync_count"]) == (2, 2, 2)
    db.rename_keys([("stock", "coins", "currency")], operation_id="second",
                   save_references=lambda: None)
    assert [r["old_key"] for r in db.get_key_renames("stock", "currency")] == ["coins", "credits"]
    with pytest.raises(ValueError, match="已有数据"):
        db.rename_keys([("stock", "currency", "reward")], operation_id="bad",
                       save_references=lambda: None)
    assert db.get_entry("tester_a", "stock", "currency") == original


def test_definition_rename_restores_files_and_sql_on_failure(tmp_path, monkeypatch):
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
    original_write = key_rename.atomic_write_text
    from lvjiang.core.profile import triggers
    with monkeypatch.context() as patch:
        patch.setattr(triggers, "_runner", SimpleNamespace(is_busy=True))
        with pytest.raises(ValueError, match="队列"):
            key_rename.save_renamed_definitions(updated, [("stock", "credits", "coins")], db=db)
    assert all(path.read_bytes() == before[path] for path in paths)

    def fail_batch(path, *args, **kwargs):
        if path == constants.BATCH_CONFIG_PATH:
            raise OSError("test write failure")
        return original_write(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(key_rename, "atomic_write_text", fail_batch)
        with pytest.raises(OSError, match="test write failure"):
            key_rename.save_renamed_definitions(updated, [("stock", "credits", "coins")], db=db)
    assert all(path.read_bytes() == before[path] for path in paths)
    assert db.get_entry("tester", "stock", "credits")["value"] == 12
    assert not db.get_key_renames("stock", "coins")
    journal_path = key_rename._journal_path(db)
    images = {str(path): base64.b64encode(content).decode() for path, content in before.items()}
    journal_path.write_text(json.dumps({"operation_id": "crashed", "files": images}))
    interface_path().write_text("{}")
    ProfileDB(db._db_path)  # 模拟 SQL 未提交时进程退出，再次启动恢复旧文件。
    assert all(path.read_bytes() == before[path] for path in paths)
    key_rename.save_renamed_definitions(updated, [("stock", "credits", "coins")], db=db)
    assert db.get_entry("tester", "stock", "coins")["value"] == 12
    assert schema.get_profile_config().get_key("reward").sync_targets[0].key == "stock:coins"
    assert json.loads(interface_path().read_text())["profile"]["overview_groups"]["group"]["columns"] == ["stock:coins"]
    assert json.loads(constants.BATCH_CONFIG_PATH.read_text())["groups"]["group"]["profile_sort_key"] == "coins"
    committed_files = {path: path.read_bytes() for path in paths}
    with db._connect() as conn:
        operation_id = conn.execute("SELECT operation_id FROM profile_key_renames").fetchone()[0]
    journal_path.write_text(json.dumps({"operation_id": operation_id, "files": images}))
    ProfileDB(db._db_path)  # SQL 已提交、仅日志清理未完成，不能回滚新文件。
    assert all(path.read_bytes() == committed_files[path] for path in paths)
    assert not journal_path.exists()
