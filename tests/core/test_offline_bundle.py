"""同步不能改变 PC 状态；损坏包或手机重载失败不能覆盖原数据。"""
import json
import sqlite3
import zipfile
from contextlib import closing

import pytest

from lvjiang.core import offline_bundle
from lvjiang.core.offline_bundle import build_offline_bundle, install_offline_bundle


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(offline_bundle, "get_registered_app_ids", lambda: ("yysls",))
    monkeypatch.setattr("lvjiang.apps.get_registered_app_ids", lambda: ("yysls",))
    source = tmp_path / "pc"
    (source / "config/system").mkdir(parents=True)
    (source / "config/system/layouts.yaml").write_text(
        "schema_version: 2\nlayouts:\n  android:\n    name: Android\n")
    session = source / "config/session"
    (session / "users").mkdir(parents=True)
    (session / "users/tester.json").write_text('{}')
    (session / "session.json").write_text(json.dumps({
        "version": 2,
        "settings": {"ai": {
            "base_url": "https://example.invalid/v1", "model": "test-model", "timeout": 30,
        }},
    }))
    (session / "interface.json").write_text(json.dumps({
        "version": 1,
        "actives": {"user": "other", "layout": "desktop", "env": "desktop"},
        "alert_history": {"tester:demo": "now"},
    }))
    (source / "config/local/diagnostics").mkdir(parents=True)
    (source / "config/local/diagnostics/private.log").write_text("excluded")
    (source / "config/local/.git").mkdir()
    (source / "config/local/.git/config").write_text("excluded")
    (source / "config/local/license.txt").write_text("synthetic-license-not-for-phone")
    archive = tmp_path / "snapshot.zip"
    # 保持 WAL 连接打开，验收快照是否含未 checkpoint 的已提交数据。
    with (closing(sqlite3.connect(session / "profile.db")) as db,
          closing(sqlite3.connect(session / "tuning_history.db")) as history):
        for connection in (db, history):
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE entries (value INTEGER)")
            connection.execute("INSERT INTO entries VALUES (7)")
            connection.commit()
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
    pc_interface = json.loads((source / "config/session/interface.json").read_text())
    assert pc_interface["actives"] == {
        "user": "other", "layout": "desktop", "env": "desktop"}
    session = json.loads((phone / "config/session/session.json").read_text())
    interface = json.loads((phone / "config/session/interface.json").read_text())
    assert interface["actives"] == {
        "user": "tester", "layout": "android", "env": "android"}
    assert session["settings"]["ai"] == pc_session["settings"]["ai"]
    assert interface["alert_history"] == {"tester:demo": "now"}
    assert "actives" not in session
    assert not (phone / "config/session/tuning_history.db").exists()
    for name in ("profile.db",):
        with closing(sqlite3.connect(phone / "config/session" / name)) as db:
            assert db.execute("SELECT value FROM entries").fetchone() == (7,)
    assert (phone / "offline-backup/config/session/old.json").read_text() == "previous"
    with zipfile.ZipFile(archive) as package:
        assert all(".git" not in name and "diagnostics" not in name for name in package.namelist())
        assert "config/local/license.txt" not in package.namelist()


def test_bundle_whitelist_preserves_runtime_assets_not_editor_files(bundle, tmp_path):
    source, _ = bundle
    included = {
        "local/workflows/custom.wf", "local/scenes/custom.yaml",
        "local/references/custom.yaml", "local/references/custom/bucket/reference.png",
        "system/templates/android/scene/match.png", "local/maps/custom/map.yaml",
        "local/maps/custom/base.png", "local/yysls/tuning_rules/custom.yaml",
        "local/yysls/gear_sets/custom.yaml", "session/users/tester.session.json",
        "session/profile.yaml", "session/yysls/play_styles.json",
    }
    excluded = {
        "session/screenshots/android/scene.png", "session/output/report.json",
        "session/yysls/unknown.json", "session/unknown.json",
        "local/data/calculator/screenshot.png", "local/data/notes.xlsx",
        "local/data/axis.json", "local/new_directory/unknown.yaml",
        "system/new_directory/unknown.yaml", "remote/new_directory/unknown.yaml",
        "local/workflows/script.py", "local/references/custom/bucket/notes.txt",
    }
    for relative in included | excluded:
        path = source / "config" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic")
    archive = tmp_path / "whitelist.zip"
    build_offline_bundle(source, archive, username="tester", layout="android")
    with zipfile.ZipFile(archive) as package:
        names = set(package.namelist())
        assert {"config/" + name for name in included} <= names
        assert not {"config/" + name for name in excluded} & names


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


def test_preservation_keeps_phone_task_choices_and_users_but_replaces_database(bundle, tmp_path):
    source, _ = bundle
    pc = source / "config/session"
    (pc / "session.json").write_text(json.dumps({"users": ["tester"], "wf_configs": {
        "existing": {"pc": True}, "new": {"new": True}}}))
    (pc / "users/tester.json").write_text(json.dumps({"attributes": {"role": "pc"}, "workflow_params": {
        "existing": {"pc": True}, "new": {"new": True}}}))
    archive = tmp_path / "keep.zip"
    build_offline_bundle(source, archive, username="tester", layout="android")
    phone = tmp_path / "phone"
    users = phone / "config/session/users"
    users.mkdir(parents=True)
    old_session = {"users": ["tester", "phone_only"],
                   "wf_configs": {"existing": {"phone": True}}}
    (users.parent / "session.json").write_text(json.dumps(old_session))
    (users.parent / "interface.json").write_text(json.dumps({
        "actives": {"user": "phone_only", "layout": "other"}}))
    for name in old_session["users"]:
        (users / f"{name}.json").write_text(json.dumps({"username": name, "attributes": {"role": "phone"},
                                                       "workflow_params": {"existing": {"phone": True}}}))
    (users.parent / "preset.json").write_text('{"preset_id":"original"}')
    install_offline_bundle(archive, phone, preserve_task_params=True)
    session = json.loads((users.parent / "session.json").read_text())
    assert session["users"] == ["tester", "phone_only"]
    assert session["wf_configs"] == {"existing": {"phone": True}, "new": {"new": True}}
    interface = json.loads((users.parent / "interface.json").read_text())
    assert interface["actives"] == {
        "user": "phone_only", "layout": "android", "env": "android"}
    user = json.loads((users / "tester.json").read_text())
    assert user["attributes"] == {"role": "pc"}
    assert user["workflow_params"] == {"existing": {"phone": True}, "new": {"new": True}}
    assert json.loads((users / "phone_only.json").read_text())["attributes"] == {"role": "phone"}
    with closing(sqlite3.connect(users.parent / "profile.db")) as db:
        assert db.execute("SELECT value FROM entries").fetchone() == (7,)
    assert json.loads((users.parent / "preset.json").read_text())["preset_id"] == "original"

    # 明确取消保留后恢复全量覆盖；APK 代次标记仍不能被 PC 同步丢掉。
    install_offline_bundle(archive, phone, preserve_task_params=False)
    session = json.loads((users.parent / "session.json").read_text())
    assert session["users"] == ["tester"]
    assert session["wf_configs"]["existing"] == {"pc": True}
    assert not (users / "phone_only.json").exists()
    assert (users.parent / "preset.json").exists()


def test_sync_uses_installed_system_and_retains_phone_archives(bundle, tmp_path):
    source, archive = bundle
    config = source / "config"
    script = config / "system/workflows/shared.wf"
    script.parent.mkdir(parents=True)
    script.write_text('log "installed update"', encoding="utf-8")
    old_remote = config / "remote/workflows/shared.wf"
    old_remote.parent.mkdir(parents=True)
    old_remote.write_text('log "obsolete"', encoding="utf-8")
    build_offline_bundle(source, archive, username="tester", layout="android")
    with zipfile.ZipFile(archive) as package:
        assert package.read("config/system/workflows/shared.wf") == script.read_bytes()
        assert not any(name.startswith("config/remote/") for name in package.namelist())
        assert "config/local/license.txt" not in package.namelist()
    phone = tmp_path / "phone"
    baseline = phone / "config/updates/v0.15.0/r0.zip"
    baseline.parent.mkdir(parents=True)
    baseline.write_bytes(b"phone official baseline")
    install_offline_bundle(archive, phone)
    assert baseline.read_bytes() == b"phone official baseline"
    assert (phone / "config/system/workflows/shared.wf").read_bytes() == script.read_bytes()


def test_sync_transfers_batch_definition_and_keeps_phone_run_draft(bundle, tmp_path):
    source, archive = bundle
    batch_definition = {"document_type": "lvjiang.batch", "version": 2, "groups": {}}
    (source / "config/session/batch.json").write_text(json.dumps(batch_definition), encoding="utf-8")
    interface = source / "config/session/interface.json"
    pc = json.loads(interface.read_text(encoding="utf-8"))
    pc["batch"] = {"active_group_id": "pc", "drafts": {"pc": {"rounds": 9}}}
    interface.write_text(json.dumps(pc), encoding="utf-8")
    build_offline_bundle(source, archive, username="tester", layout="android")
    phone = tmp_path / "phone"
    state = phone / "config/session/interface.json"
    state.parent.mkdir(parents=True)
    mobile = {"active_group_id": "phone", "drafts": {"phone": {"rounds": 2}}}
    state.write_text(json.dumps({"batch": mobile}), encoding="utf-8")
    install_offline_bundle(archive, phone, preserve_task_params=False)
    assert json.loads((state.parent / "batch.json").read_text(encoding="utf-8")) == batch_definition
    assert json.loads(state.read_text(encoding="utf-8"))["batch"] == mobile


def test_batch_definition_preservation_uses_explicit_sync_choice(bundle, tmp_path):
    source, archive = bundle
    pc = {"document_type": "lvjiang.batch", "version": 2, "groups": {}}
    (source / "config/session/batch.json").write_text(json.dumps(pc), encoding="utf-8")
    pc_session = source / "config/session/session.json"
    session = json.loads(pc_session.read_text(encoding="utf-8"))
    session["wf_configs"] = {"task": {"value": "pc"}}
    pc_session.write_text(json.dumps(session), encoding="utf-8")
    build_offline_bundle(source, archive, username="tester", layout="android")
    phone = tmp_path / "phone"
    config = phone / "config/session/batch.json"
    config.parent.mkdir(parents=True)
    mobile = {"document_type": "lvjiang.batch", "version": 2, "groups": {"mobile": {"name": "手机配置"}}}
    config.write_text(json.dumps(mobile), encoding="utf-8")
    phone_session = config.parent / "session.json"
    phone_session.write_text(json.dumps({"users": ["tester"], "wf_configs": {"task": {"value": "phone"}}}),
                             encoding="utf-8")
    # 更新参数但保留批量配置，两项选择互不影响。
    install_offline_bundle(archive, phone, preserve_task_params=False, preserve_batch_config=True)
    assert json.loads(phone_session.read_text(encoding="utf-8"))["wf_configs"]["task"]["value"] == "pc"
    assert json.loads(config.read_text(encoding="utf-8")) == mobile
    phone_session.write_text(json.dumps({"users": ["tester"], "wf_configs": {"task": {"value": "phone"}}}),
                             encoding="utf-8")
    # 更新批量配置但保留参数，防止新组仍被手机旧文件遮蔽。
    install_offline_bundle(archive, phone, preserve_task_params=True, preserve_batch_config=False)
    assert json.loads(phone_session.read_text(encoding="utf-8"))["wf_configs"]["task"]["value"] == "phone"
    assert json.loads(config.read_text(encoding="utf-8")) == pc


def test_downstream_preserves_phone_history_and_result_files(bundle, tmp_path):
    _, archive = bundle
    phone = tmp_path / "phone"
    session = phone / "config/session"
    session.mkdir(parents=True)
    result = session / "output/user/2026-01/01/result.json"
    result.parent.mkdir(parents=True)
    result.write_text('{"结果": true}\n', encoding="utf-8")
    with (closing(sqlite3.connect(session / "daily_history.db")) as tasks,
          closing(sqlite3.connect(session / "tuning_history.db")) as tuning):
        for conn in (tasks, tuning):
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("CREATE TABLE records (value TEXT)")
            conn.execute("INSERT INTO records VALUES ('phone')")
            conn.commit()
    install_offline_bundle(archive, phone, preserve_task_params=False)
    for name in ("daily_history.db", "tuning_history.db"):
        with closing(sqlite3.connect(session / name)) as conn:
            assert conn.execute("SELECT value FROM records").fetchall() == [("phone",)]
    assert json.loads(result.read_text(encoding="utf-8")) == {"结果": True}
    with zipfile.ZipFile(archive) as package:
        assert "config/session/daily_history.db" not in package.namelist()
        assert "config/session/tuning_history.db" not in package.namelist()
