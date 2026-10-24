"""断网不能变成撤回；公开与会员两个下载渠道互不拖累。"""
from lvjiang.core.config import remote
from lvjiang.core.config.member_download import MemberDownload


def test_public_download_failure_preserves_member_candidate(tmp_path, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(remote, "UpdateStore", lambda *args: SimpleNamespace(
        install=lambda *a, **kw: (), prune_archives=lambda: None, _state=lambda: {}))
    member = MemberDownload({}, {})

    def unavailable(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(remote, "fetch_public_update", unavailable)
    monkeypatch.setattr(remote, "fetch_member_update", lambda *args, **kwargs: member)
    result = remote.run_sync(remote.SyncJob(config=tmp_path, app_version="0.15.0"))
    assert result.public is None
    assert result.member is member
    assert len(result.errors) == 1


def test_unknown_authorization_does_not_withdraw_member(tmp_path, monkeypatch):
    monkeypatch.setattr(remote, "fetch_public_update", lambda *args, **kwargs: None)

    def unexpected(*args, **kwargs):
        raise AssertionError("unknown authorization must not be interpreted as absent")

    monkeypatch.setattr(remote, "fetch_member_update", unexpected)
    result = remote.run_sync(remote.SyncJob(config=tmp_path, app_version="0.15.0",
                                           member_access_known=False))
    assert result.member is None
    assert not result.errors


def test_background_download_then_activation_on_main_thread(tmp_path, monkeypatch):
    from lvjiang.core.config import protected_remote, update_activation
    from lvjiang.core.config.update_download import PublicDownload
    from lvjiang.core.update_bundle import json_bytes, make_manifest, tree_digest

    system = tmp_path / "system"
    system.mkdir()
    (system / "opaque.bin").write_bytes(b"old")
    local = tmp_path / "local"
    local.mkdir()
    (local / "app.yaml").write_text("value: 9", encoding="utf-8")
    baseline = {"opaque.bin": b"old"}
    manifest, archive = make_manifest("0.15.0", 1, baseline, {"opaque.bin": b"new"})
    monkeypatch.setattr(remote, "fetch_public_update", lambda *a, **kw: PublicDownload(json_bytes(manifest), archive))
    monkeypatch.setattr(remote, "fetch_member_update", lambda *a, **kw: MemberDownload({}, {}))
    monkeypatch.setattr(remote, "is_enabled", lambda: True)
    monkeypatch.setattr(protected_remote, "snapshot", lambda: None)
    activated = []
    monkeypatch.setattr(update_activation, "activate_configuration", lambda path, changed: activated.append(changed))
    job = remote.SyncJob(tmp_path, "0.15.0", tree_digest(baseline))
    result = remote.run_sync(job)
    # 下载不触碰生效目录。
    assert (system / "opaque.bin").read_bytes() == b"old"
    # 安装不审查或修改 local，本地编辑不能阻断官方包安装。
    (local / "app.yaml").write_text("value: 99", encoding="utf-8")
    (local / "diagnostics").mkdir()
    (local / "diagnostics/runtime.log").write_text("task is running", encoding="utf-8")
    assert remote.apply_outcome(result) == ("opaque.bin",)
    assert activated == [("opaque.bin",)]
    assert (local / "app.yaml").read_text(encoding="utf-8") == "value: 99"
    # 同修订再次同步不重新解析或切换目录。
    remote.apply_outcome(remote.run_sync(job))
    assert activated == [("opaque.bin",)]


def test_successful_empty_index_withdraws_public_but_failure_preserves_it(tmp_path, monkeypatch):
    from lvjiang.core.config import protected_remote, update_activation
    from lvjiang.core.config.update_store import UpdateStore
    from lvjiang.core.update_bundle import json_bytes, make_manifest, tree_digest

    (tmp_path / "system").mkdir()
    (tmp_path / "local").mkdir()
    base = {"opaque.bin": b"base"}
    (tmp_path / "system/opaque.bin").write_bytes(b"base")
    store = UpdateStore(tmp_path, "0.15.0", tree_digest(base))
    manifest, archive = make_manifest("0.15.0", 2, base, {"opaque.bin": b"updated"})
    store.install(manifest, archive, activate=lambda *a: None)
    monkeypatch.setattr(remote, "is_enabled", lambda: True)
    monkeypatch.setattr(protected_remote, "snapshot", lambda: None)
    monkeypatch.setattr(update_activation, "activate_configuration", lambda *a: None)
    def unavailable(*a, **kw):
        raise OSError("offline")
    monkeypatch.setattr(remote, "_fetch_bytes", unavailable)
    job = remote.SyncJob(tmp_path, "0.15.0", tree_digest(base))
    remote.apply_outcome(remote.run_sync(job))
    assert store._state()["revision"] == 2
    assert (store.system / "opaque.bin").read_bytes() == b"updated"
    empty = json_bytes({"schema_version": 1, "series": {}})
    monkeypatch.setattr(remote, "_fetch_bytes", lambda *a, **kw: (empty, "", False))
    result = remote.run_sync(job)
    # 成功的空索引是真正的线上撤销，可回退到 r0。
    remote.apply_outcome(result)
    assert store._state()["revision"] == 0
    assert (store.system / "opaque.bin").read_bytes() == b"base"
    assert (store.root / "public/index.json").read_bytes() == empty
