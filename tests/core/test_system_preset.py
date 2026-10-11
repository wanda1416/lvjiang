"""官方预置的持续契约：可复核、无个人配置、升级只改 system、失败回滚。"""
import json
import zipfile

import pytest

from lvjiang.core.system_preset import build_system_preset, install_system_preset


@pytest.fixture
def official(tmp_path):
    system = tmp_path / "official/system"
    system.mkdir(parents=True)
    for name in ("app.yaml", "ocr.yaml", "layouts.yaml"):
        (system / name).write_text("{}", encoding="utf-8")
    (system / "workflows").mkdir()
    (system / "workflows/task.wf").write_text("wait 0.01", encoding="utf-8")
    (system.parent / "local").mkdir()
    (system.parent / "local/private.yaml").write_text("not bundled", encoding="utf-8")
    return system


def test_package_is_deterministic_and_only_contains_official_configuration(official, tmp_path):
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    build_system_preset(official, first)
    build_system_preset(official, second)
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as package:
        assert set(package.namelist()) == {"system/app.yaml", "system/ocr.yaml", "system/layouts.yaml", "system/workflows/task.wf", "manifest.json"}


def test_packaging_only_writes_identity_and_startup_creates_r0(official, monkeypatch):
    from lvjiang.core.config import remote
    from lvjiang.core.config.update_store import UpdateStore
    from lvjiang.core.system_preset import write_baseline_identity
    from lvjiang.core.update_bundle import read_tree, unpack
    identity = write_baseline_identity(official, "0.15.0")
    store = UpdateStore(official.parent, "0.15.0", identity["sha256"])
    baseline = store.root / "r0.zip"
    assert not baseline.exists()
    monkeypatch.setattr(remote, "_installed_store", lambda: store)
    remote.recover_installation()
    assert unpack(baseline.read_bytes()) == read_tree(official)
    assert store._state()["revision"] == 0


def test_upgrade_preserves_parameters_database_and_pc_sync_until_new_preset(official, tmp_path, monkeypatch):
    archive = tmp_path / "preset.zip"
    build_system_preset(official, archive)
    phone = tmp_path / "phone"
    assert install_system_preset(archive, phone)
    session = phone / "config/session"
    (session / "session.json").write_text('{"wf_configs":{"task":{"count":5}}}', encoding="utf-8")
    (session / "profile.db").write_bytes(b"unchanged database")
    (phone / "config/local").mkdir()
    (phone / "config/local/override.yaml").write_text("local", encoding="utf-8")
    (phone / "config/system/workflows/task.wf").write_text("PC synced task", encoding="utf-8")
    assert not install_system_preset(archive, phone)
    assert (phone / "config/system/workflows/task.wf").read_text(encoding="utf-8") == "PC synced task"
    (official / "workflows/task.wf").unlink()
    (official / "workflows/new.wf").write_text("wait 0.02", encoding="utf-8")
    monkeypatch.setattr("lvjiang.core.system_preset.__version__", "9.1.0")
    build_system_preset(official, archive)
    assert install_system_preset(archive, phone)
    assert not (phone / "config/system/workflows/task.wf").exists()
    assert (phone / "config/system/workflows/new.wf").is_file()
    assert json.loads((session / "session.json").read_text(encoding="utf-8"))["wf_configs"]["task"]["count"] == 5
    assert (session / "profile.db").read_bytes() == b"unchanged database"
    assert (phone / "config/local/override.yaml").read_text(encoding="utf-8") == "local"


def test_failed_reload_restores_previous_system_and_install_marker(official, tmp_path):
    archive = tmp_path / "preset.zip"
    build_system_preset(official, archive)
    phone = tmp_path / "phone"
    install_system_preset(archive, phone)
    marker = (phone / "config/session/preset.json").read_bytes()
    (official / "workflows/task.wf").write_text("wait 2", encoding="utf-8")
    build_system_preset(official, archive)
    def fail():
        raise RuntimeError("reload failed")
    with pytest.raises(RuntimeError, match="reload failed"):
        install_system_preset(archive, phone, on_applied=fail)
    assert (phone / "config/system/workflows/task.wf").read_text(encoding="utf-8") == "wait 0.01"
    assert (phone / "config/session/preset.json").read_bytes() == marker
