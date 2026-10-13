"""同步只携带已装配插件声明的数据库，不由通用层推断业务文件。"""
import sqlite3
import zipfile
from contextlib import closing

from lvjiang.apps.base import AppHooks
from lvjiang.core import offline_bundle


def test_database_selection_uses_plugin_declarations(tmp_path, monkeypatch):
    hooks = {"sample": AppHooks(id="sample", offline_database_files=("sample.db", "profile.db"))}
    monkeypatch.setattr(offline_bundle, "get_registered_app_ids", lambda: ("sample",))
    monkeypatch.setattr(offline_bundle, "load_app", hooks.__getitem__)
    config = tmp_path / "config"
    (config / "system").mkdir(parents=True)
    (config / "system/layouts.yaml").write_text(
        "schema_version: 2\nlayouts:\n  android:\n    name: Android\n", encoding="utf-8")
    (config / "session/users").mkdir(parents=True)
    (config / "session/users/tester.json").write_text("{}", encoding="utf-8")
    for name in ("profile.db", "sample.db", "unregistered.db"):
        with closing(sqlite3.connect(config / "session" / name)) as database:
            database.execute("CREATE TABLE sample (value INTEGER)")
            database.commit()
    archive = tmp_path / "snapshot.zip"
    offline_bundle.build_offline_bundle(tmp_path, archive, username="tester", layout="android")
    with zipfile.ZipFile(archive) as package:
        assert "config/session/sample.db" in package.namelist()
        assert "config/session/profile.db" in package.namelist()
        assert "config/session/unregistered.db" not in package.namelist()
    monkeypatch.setattr(offline_bundle, "get_registered_app_ids", tuple)
    offline_bundle.build_offline_bundle(tmp_path, archive, username="tester", layout="android")
    with zipfile.ZipFile(archive) as package:
        assert "config/session/sample.db" not in package.namelist()
        assert "config/session/profile.db" in package.namelist()
