"""在线配置只在读取场景之前安装；失败时保留当前磁盘配置。"""
from lvjiang.core import scene_registry
from lvjiang.core.config import remote
from lvjiang.core.config import resolver as resolver_module
from lvjiang.core.config.member_download import MemberDownload
from lvjiang.core.config.resolver import ConfigResolver
from lvjiang.core.config.update_download import PublicDownload
from lvjiang.core.system_preset import write_baseline_identity
from lvjiang.core.update_bundle import json_bytes, make_manifest, read_tree


def _release_resolver(tmp_path, monkeypatch):
    system = tmp_path / "system"
    system.mkdir()
    (system / "opaque.bin").write_bytes(b"old")
    resolver = ConfigResolver(system, tmp_path / "local", dev_mode=False, config_dir=tmp_path)
    monkeypatch.setattr(resolver_module, "_resolver", resolver)
    monkeypatch.setattr("lvjiang.core.update.get_version", lambda: "0.15.0")
    write_baseline_identity(system, "0.15.0", config_dir=tmp_path)
    monkeypatch.setattr(remote, "is_enabled", lambda: True)
    monkeypatch.setattr("lvjiang.core.config.protected_remote.snapshot", lambda: None)
    return system


def test_startup_installs_public_package_without_reloading_scenes(tmp_path, monkeypatch):
    system = _release_resolver(tmp_path, monkeypatch)
    files = read_tree(system)
    manifest, archive = make_manifest("0.15.0", 1, files, {"opaque.bin": b"new"})
    monkeypatch.setattr(
        remote, "fetch_public_update",
        lambda *_a, **_k: PublicDownload(json_bytes(manifest), archive))
    monkeypatch.setattr(remote, "fetch_member_update", lambda *_a, **_k: MemberDownload({}, {}))

    def reject_scene_reload(*_args, **_kwargs):
        raise AssertionError("启动安装不得重载已经存在的场景注册表")

    monkeypatch.setattr(scene_registry, "reload_scene_registry", reject_scene_reload)
    monkeypatch.setattr(scene_registry, "initialize_scene_registry", reject_scene_reload)
    remote.prepare_startup_configuration()
    assert (system / "opaque.bin").read_bytes() == b"new"


def test_startup_download_failure_keeps_installed_files(tmp_path, monkeypatch):
    system = _release_resolver(tmp_path, monkeypatch)

    def unavailable(*_args, **_kwargs):
        raise OSError("offline")

    monkeypatch.setattr(remote, "_fetch_bytes", unavailable)
    remote.prepare_startup_configuration()
    assert (system / "opaque.bin").read_bytes() == b"old"
