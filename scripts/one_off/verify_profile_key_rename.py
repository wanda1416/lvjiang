"""手工验证最新 session 的 Profile 迁移及四模型重命名；只修改临时副本。

用法：python scripts/one_off/verify_profile_key_rename.py config/session
不属于 CI。报告仅包含类型与数量，不输出用户、key 或原始记录。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import tempfile
from copy import deepcopy
from pathlib import Path

from loguru import logger

from lvjiang import constants
from lvjiang.apps.yysls.core import season_periods  # noqa: F401 注册业务周期
from lvjiang.core.config.session import reset_session_store
from lvjiang.core.profile import repository, schema
from lvjiang.core.profile.key_rename import save_renamed_definitions
from lvjiang.core.profile.models import ALL_MODELS
from lvjiang.core.profile.repository import ProfileDB


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(source: Path) -> None:
    logger.remove()  # 私有路径、用户与 key 不进入验证报告。
    originals = {path: digest(path) for path in source.iterdir() if path.is_file()}
    with tempfile.TemporaryDirectory(prefix="profile-rename-verify-") as folder:
        root = Path(folder)
        for name in ("profile.yaml", "session.json", "batch.json"):
            if (source / name).exists():
                shutil.copy2(source / name, root / name)
        path = root / "profile.db"
        with sqlite3.connect(f"file:{source / 'profile.db'}?mode=ro", uri=True) as src:
            with sqlite3.connect(path) as dst:
                src.backup(dst)
            old_rows = src.execute("SELECT id,ts,username,type,key,old_value,new_value,old_value_text,new_value_text,source FROM profile_history ORDER BY id").fetchall()
            old_entries = src.execute("SELECT * FROM profile_entries ORDER BY username,type,key").fetchall()
            reset_count = src.execute("SELECT COUNT(*) FROM profile_history WHERE change_type='tick' AND detail='reset:0'").fetchone()[0]
        constants.SESSION_CONFIG_DIR = root
        constants.SESSION_PATH = root / "session.json"
        constants.BATCH_CONFIG_PATH = root / "batch.json"
        schema._PROFILE_PATH = root / "profile.yaml"
        schema._config = None
        repository._DB_PATH = path
        repository.reset_profile_db()
        reset_session_store()
        db = repository.get_profile_db()
        with db._connect() as conn:
            assert conn.execute("SELECT id,ts,username,type,key,old_value,new_value,old_value_text,new_value_text,source FROM profile_history ORDER BY id").fetchall() == old_rows
            assert conn.execute("SELECT * FROM profile_entries ORDER BY username,type,key").fetchall() == old_entries
            assert conn.execute("SELECT COUNT(*) FROM profile_history WHERE change_type='reset'").fetchone()[0] == reset_count
            unknown = conn.execute("SELECT COUNT(*) FROM profile_history_legacy").fetchone()[0]
        print(f"迁移通过：历史 {len(old_rows)} 条，reset {reset_count} 条，未识别原文归档 {unknown} 条")
        for model in ALL_MODELS:
            config = schema.get_profile_config()
            valid = {kd.key for kd in config.get_keys_by_model(model)}
            with db._connect() as conn:
                ranked = conn.execute("SELECT key,COUNT(*) FROM profile_history WHERE type=? GROUP BY key ORDER BY COUNT(*) DESC", (model,)).fetchall()
                selected = next(((key, count) for key, count in ranked if key in valid), None)
                assert selected is not None, f"{model} 没有可验证的已定义 key"
                old, count = selected
                new = "verify_renamed_" + model
                source_history = db.get_history(None, model, old, limit=count + 1)
                before_entries = conn.execute("SELECT username,value,value_text,updated_at,updated_time FROM profile_entries WHERE type=? AND key=? ORDER BY username", (model, old)).fetchall()
                sync_count = conn.execute("SELECT COUNT(*) FROM profile_history WHERE sync_from IN (?,?)", (old, f"{model}:{old}")).fetchone()[0]
            updated = deepcopy(config)
            for kd in updated.keys_by_model[model]:
                if kd.key == old:
                    kd.key = new
            updated._rebuild_index()
            save_renamed_definitions(updated, [(model, old, new)], db=db)
            after_history = db.get_history(None, model, new, limit=count + 1)
            assert len(after_history) == count
            for before, after in zip(source_history, after_history, strict=True):
                assert {k: v for k, v in before.items() if k not in ("key", "sync_from")} == {k: v for k, v in after.items() if k not in ("key", "sync_from")}
            with db._connect() as conn:
                assert conn.execute("SELECT username,value,value_text,updated_at,updated_time FROM profile_entries WHERE type=? AND key=? ORDER BY username", (model, new)).fetchall() == before_entries
                assert not conn.execute("SELECT 1 FROM profile_history WHERE type=? AND key=?", (model, old)).fetchone()
                assert conn.execute("SELECT COUNT(*) FROM profile_history WHERE sync_from=?", (f"{model}:{new}",)).fetchone()[0] == sync_count
            audit = db.get_key_renames(model, new)[0]
            assert (audit["entries_count"], audit["history_count"], audit["sync_count"]) == (len(before_entries), count, sync_count)
            assert schema.get_profile_config().get_key(new, model_type=model)
            print(json.dumps({"model": model, "entries": len(before_entries), "history": count, "sync_refs": sync_count, "audit": "passed"}, ensure_ascii=False))
        assert len(ProfileDB(path).get_history(None, limit=len(old_rows) + 1)) == len(old_rows)
    assert all(digest(path) == expected for path, expected in originals.items())
    print("原始 session 文件哈希全部一致；临时副本已清理")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_dir", type=Path)
    verify(parser.parse_args().session_dir.resolve())
