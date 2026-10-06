"""同步不能改变 PC 状态；损坏包或手机重载失败不能覆盖原数据。"""
import json
import sqlite3
import zipfile
from contextlib import closing

import pytest

from lvjiang.core import offline_bundle
from lvjiang.core.offline_bundle import build_offline_bundle, install_offline_bundle


@pytest.fixture
def bundle(tmp_path):
    source = tmp_path / "pc"
    (source / "config/system").mkdir(parents=True)
    (source / "config/system/layouts.yaml").write_text(
        "schema_version: 2\nlayouts:\n  android:\n    name: Android\n")
    session = source / "config/session"
    (session / "users").mkdir(parents=True)
    (session / "users/tester.json").write_text('{}')
    (session / "session.json").write_text(json.dumps({
        "version": 2, "actives": {"user": "other", "layout": "desktop"},
        "settings": {"env": "desktop"},
    }))
    (source / "config/local/diagnostics").mkdir(parents=True)
    (source / "config/local/diagnostics/private.log").write_text("excluded")
    (source / "config/local/.git").mkdir()
    (source / "config/local/.git/config").write_text("excluded")
    (source / "config/local/license.txt").write_text("synthetic-license-not-for-phone")
    archive = tmp_path / "snapshot.zip"
    # 保持 WAL 连接打开，验收快照是否含未 checkpoint 的已提交数据。
    with closing(sqlite3.connect(session / "profile.db")) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE entries (value INTEGER)")
        db.execute("INSERT INTO entries VALUES (7)")
        db.commit()
        build_offline_bundle(source, archive, username="tester", layout="android")
    return source, archive


def test_sync_copies_wal_database_without_changing_pc_selection(bundle, tmp_path):
    source, archive = bundle
    phone = tmp_path / "phone"
    old = phone / "config/session"
    old.mkdir(parents=True)
    (old / "old.json").write_text("previous")

    install_offline_bundle(archive, phone)

    pc_session = json.loads((source / "config/session/session.json").read_text())
    assert pc_session["actives"] == {"user": "other", "layout": "desktop"}
    assert pc_session["settings"]["env"] == "desktop"
    session = json.loads((phone / "config/session/session.json").read_text())
    assert session["actives"] == {"user": "tester", "layout": "android"}
    assert session["settings"]["env"] == "android"
    with closing(sqlite3.connect(phone / "config/session/profile.db")) as db:
        assert db.execute("SELECT value FROM entries").fetchone() == (7,)
    assert (phone / "offline-backup/config/session/old.json").read_text() == "previous"
    with zipfile.ZipFile(archive) as package:
        assert all(".git" not in name and "diagnostics" not in name for name in package.namelist())
        assert "config/local/license.txt" not in package.namelist()


def test_bundle_keeps_all_users_and_tasks_but_only_selected_layout_dependencies(bundle, tmp_path):
    import yaml

    source, _ = bundle
    config = source / "config"
    layouts = {"android": {"name": "Android"}, "cast": {"name": "Cast", "extends": "android"},
               "desktop": {"name": "Desktop"}}
    (config / "system/layouts.yaml").write_text(yaml.safe_dump({"schema_version": 2, "layouts": layouts}))
    for name in layouts:
        folder = config / "system/layouts" / name
        folder.mkdir(parents=True)
        (folder / "scene.json").write_text('{}')
    (config / "session/users/second.json").write_text('{}')
    workflows = config / "system/workflows"
    workflows.mkdir()
    (workflows / "first.wf").write_text("wait 1")
    (workflows / "second.wf").write_text("wait 2")
    archive = tmp_path / "selected.zip"
    build_offline_bundle(source, archive, username="tester", layout="cast")
    with zipfile.ZipFile(archive) as package:
        names = package.namelist()
        assert "config/session/users/second.json" in names
        assert "config/system/workflows/first.wf" in names
        assert "config/system/workflows/second.wf" in names
        assert "config/system/layouts/desktop/scene.json" not in names
        assert "config/system/layouts/android/scene.json" in names
        assert "config/system/layouts/cast/scene.json" in names
        assert set(yaml.safe_load(package.read("config/system/layouts.yaml"))["layouts"]) == {"cast", "android"}
    assert set(yaml.safe_load((config / "system/layouts.yaml").read_text())["layouts"]) == set(layouts)


def test_corrupted_package_preserves_phone_configuration(bundle, tmp_path):
    _, archive = bundle
    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(archive) as original, zipfile.ZipFile(broken, "w") as target:
        for name in original.namelist():
            target.writestr(name, b"tampered" if name.endswith("tester.json") else original.read(name))
    phone = tmp_path / "phone"
    (phone / "config").mkdir(parents=True)
    (phone / "config/keep").write_text("original")

    with pytest.raises(ValueError, match="校验失败"):
        install_offline_bundle(broken, phone)

    assert (phone / "config/keep").read_text() == "original"
    assert not (phone / "config/session").exists()


@pytest.fixture
def connections(monkeypatch):
    """保留连接引用，避免 GC 在 Linux 上掩盖 Windows 文件句柄泄漏。"""
    opened = []
    connect = sqlite3.connect

    def track(*args, **kwargs):
        connection = connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(offline_bundle.sqlite3, "connect", track)
    yield opened
    for connection in opened:
        connection.close()


def test_snapshot_and_integrity_check_release_database_handles(bundle, connections, tmp_path):
    source, _ = bundle
    archive = tmp_path / "handles.zip"
    build_offline_bundle(source, archive, username="tester", layout="android")

    assert connections
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")

    snapshot_connections = len(connections)

    def before_directory_swap_finishes():
        assert len(connections) > snapshot_connections
        for connection in connections:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                connection.execute("SELECT 1")

    install_offline_bundle(archive, tmp_path / "phone", on_applied=before_directory_swap_finishes)


def test_snapshot_creation_failure_releases_source_handle(bundle, connections, monkeypatch, tmp_path):
    source, _ = bundle
    connect = offline_bundle.sqlite3.connect

    def fail_target_creation(*args, **kwargs):
        if connections:
            raise sqlite3.OperationalError("snapshot creation failed")
        return connect(*args, **kwargs)

    monkeypatch.setattr(offline_bundle.sqlite3, "connect", fail_target_creation)
    with pytest.raises(sqlite3.OperationalError, match="snapshot creation failed"):
        build_offline_bundle(source, tmp_path / "failure.zip", username="tester", layout="android")

    assert connections
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_reload_failure_rolls_back_applied_configuration(bundle, tmp_path):
    _, archive = bundle
    phone = tmp_path / "phone"
    (phone / "config").mkdir(parents=True)
    (phone / "config/keep").write_text("original")

    def reload_failed():
        assert (phone / "config/session/profile.db").exists()
        raise RuntimeError("reload failed")

    with pytest.raises(RuntimeError, match="reload failed"):
        install_offline_bundle(archive, phone, on_applied=reload_failed)

    assert (phone / "config/keep").read_text() == "original"
    assert not (phone / "config/session").exists()
