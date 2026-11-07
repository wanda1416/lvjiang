"""在线配置只在读取场景之前安装上次下载好的候选；启动不再联网。"""
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


def _stage_revision(tmp_path, monkeypatch, system, *, content=b"new"):
    files = read_tree(system)
    manifest, archive = make_manifest("0.15.0", 1, files, {"opaque.bin": content})
    monkeypatch.setattr(
        remote, "fetch_public_update",
        lambda *_a, **_k: PublicDownload(json_bytes(manifest), archive))
    monkeypatch.setattr(remote, "fetch_member_update", lambda *_a, **_k: MemberDownload({}, {}))
    remote.stage_outcome(remote.run_sync(remote.build_sync_job()))


def _forbid_network(monkeypatch):
    def offline(*_args, **_kwargs):
        raise AssertionError("启动安装不得联网")

    monkeypatch.setattr(remote, "fetch_public_update", offline)
    monkeypatch.setattr(remote, "fetch_member_update", offline)
    monkeypatch.setattr(remote, "_fetch_bytes", offline)


def test_background_download_stages_without_installing(tmp_path, monkeypatch):
    system = _release_resolver(tmp_path, monkeypatch)
    _stage_revision(tmp_path, monkeypatch, system)
    # 下载只落暂存区；生效目录必须保持原样，等下次启动才安装。
    assert (system / "opaque.bin").read_bytes() == b"old"


def test_startup_installs_staged_candidate_without_network(tmp_path, monkeypatch):
    system = _release_resolver(tmp_path, monkeypatch)
    _stage_revision(tmp_path, monkeypatch, system)
    _forbid_network(monkeypatch)

    def reject_scene_reload(*_args, **_kwargs):
        raise AssertionError("启动安装不得重载已经存在的场景注册表")

    monkeypatch.setattr(scene_registry, "reload_scene_registry", reject_scene_reload)
    monkeypatch.setattr(scene_registry, "initialize_scene_registry", reject_scene_reload)
    remote.prepare_startup_configuration()
    assert (system / "opaque.bin").read_bytes() == b"new"


def test_startup_without_candidate_keeps_installed_files(tmp_path, monkeypatch):
    system = _release_resolver(tmp_path, monkeypatch)
    _forbid_network(monkeypatch)
    remote.prepare_startup_configuration()
    assert (system / "opaque.bin").read_bytes() == b"old"


def test_prune_keeps_downloaded_candidate_until_next_startup(tmp_path, monkeypatch):
    system = _release_resolver(tmp_path, monkeypatch)
    _stage_revision(tmp_path, monkeypatch, system)
    store = remote._installed_store()
    assert store is not None
    # 后台线程每轮先 prune；未安装的候选不能被当成无主归档回收掉。
    store.prune_archives()
    assert store.pending_public() is not None
    _forbid_network(monkeypatch)
    remote.prepare_startup_configuration()
    assert (system / "opaque.bin").read_bytes() == b"new"


def test_disabling_switch_restores_preset_on_next_startup(tmp_path, monkeypatch):
    system = _release_resolver(tmp_path, monkeypatch)
    _stage_revision(tmp_path, monkeypatch, system)
    remote.prepare_startup_configuration()
    assert (system / "opaque.bin").read_bytes() == b"new"
    monkeypatch.setattr(remote, "is_enabled", lambda: False)
    remote.prepare_startup_configuration()
    assert (system / "opaque.bin").read_bytes() == b"old"
