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
        assert set(package.namelist()) == {"app.yaml", "ocr.yaml", "layouts.yaml", "workflows/task.wf", "manifest.json"}


def test_upgrade_preserves_parameters_database_and_pc_sync_until_new_preset(official, tmp_path):
    archive = tmp_path / "preset.zip"
    build_system_preset(official, archive)
    phone = tmp_path / "phone"
    assert install_system_preset(archive, phone)
    session = phone / "config/session"
    (session / "session.json").write_text('{"wf_configs":{"task":{"count":5}}}')
    (session / "profile.db").write_bytes(b"unchanged database")
    (phone / "config/local").mkdir()
    (phone / "config/local/override.yaml").write_text("local")
    (phone / "config/system/workflows/task.wf").write_text("PC synced task")
    assert not install_system_preset(archive, phone)
    assert (phone / "config/system/workflows/task.wf").read_text() == "PC synced task"
    (official / "workflows/task.wf").unlink()
    (official / "workflows/new.wf").write_text("wait 0.02")
    build_system_preset(official, archive)
    assert install_system_preset(archive, phone)
    assert not (phone / "config/system/workflows/task.wf").exists()
    assert (phone / "config/system/workflows/new.wf").is_file()
    assert json.loads((session / "session.json").read_text())["wf_configs"]["task"]["count"] == 5
    assert (session / "profile.db").read_bytes() == b"unchanged database"
    assert (phone / "config/local/override.yaml").read_text() == "local"


def test_failed_reload_restores_previous_system_and_install_marker(official, tmp_path):
    archive = tmp_path / "preset.zip"
    build_system_preset(official, archive)
    phone = tmp_path / "phone"
    install_system_preset(archive, phone)
    marker = (phone / "config/session/preset.json").read_bytes()
    (official / "workflows/task.wf").write_text("wait 2")
    build_system_preset(official, archive)
    def fail():
        raise RuntimeError("reload failed")
    with pytest.raises(RuntimeError, match="reload failed"):
        install_system_preset(archive, phone, on_applied=fail)
    assert (phone / "config/system/workflows/task.wf").read_text() == "wait 0.01"
    assert (phone / "config/session/preset.json").read_bytes() == marker
