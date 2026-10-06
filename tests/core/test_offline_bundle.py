"""同步不能改变 PC 状态；损坏包或手机重载失败不能覆盖原数据。"""
import json
import sqlite3
import zipfile

import pytest

from lvjiang.core.offline_bundle import build_offline_bundle, install_offline_bundle


@pytest.fixture
def bundle(tmp_path):
    source = tmp_path / "pc"
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
    archive = tmp_path / "snapshot.zip"
    # 保持 WAL 连接打开，验收快照是否含未 checkpoint 的已提交数据。
    with sqlite3.connect(session / "profile.db") as db:
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
    with sqlite3.connect(phone / "config/session/profile.db") as db:
        assert db.execute("SELECT value FROM entries").fetchone() == (7,)
    assert (phone / "offline-backup/config/session/old.json").read_text() == "previous"
    with zipfile.ZipFile(archive) as package:
        assert all(".git" not in name and "diagnostics" not in name for name in package.namelist())


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
