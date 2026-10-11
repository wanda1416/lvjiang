"""断网不能变成撤回；公开与会员两个下载渠道互不拖累。"""
from pathlib import Path

import pytest

from lvjiang.core.config import remote
from lvjiang.core.config.member_download import MemberDownload


def test_public_download_failure_preserves_member_candidate(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(remote, "UpdateStore", lambda *args: SimpleNamespace(install=lambda *a, **kw: ()))
    member = MemberDownload({}, {})

    def unavailable(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(remote, "fetch_public_update", unavailable)
    monkeypatch.setattr(remote, "fetch_member_update", lambda *args, **kwargs: member)
    result = remote.run_sync(remote.SyncJob(config=Path("unused"), app_version="0.15.0"))
    assert result.public is None
    assert result.member is member
    assert len(result.errors) == 1


def test_unknown_authorization_does_not_withdraw_member(monkeypatch):
    monkeypatch.setattr(remote, "fetch_public_update", lambda *args, **kwargs: None)

    def unexpected(*args, **kwargs):
        raise AssertionError("unknown authorization must not be interpreted as absent")

    monkeypatch.setattr(remote, "fetch_member_update", unexpected)
    result = remote.run_sync(remote.SyncJob(config=Path("unused"), app_version="0.15.0",
                                           member_access_known=False))
    assert result.member is None
    assert not result.errors


def test_background_validation_then_activation_and_other_instance_refresh(tmp_path, monkeypatch):
    from lvjiang.core.config import protected_remote, update_activation
    from lvjiang.core.config.update_download import PublicDownload
    from lvjiang.core.config.update_observer import InstallationObserver
    from lvjiang.core.config.update_store import UpdateStore
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
    checks = []
    monkeypatch.setattr(update_activation, "validate_candidate",
                        lambda path, *a: checks.append((path / "opaque.bin").read_bytes()))
    activated = []
    monkeypatch.setattr(update_activation, "activate_configuration", lambda path, changed: activated.append(changed))
    observer = InstallationObserver(UpdateStore(tmp_path, "0.15.0", tree_digest(baseline)))
    job = remote.SyncJob(tmp_path, "0.15.0", tree_digest(baseline))
    result = remote.run_sync(job)
    assert checks == [b"new"]
    assert (system / "opaque.bin").read_bytes() == b"old"
    # 下载与安装之间发生本地编辑，不能使用过时校验结论。
    (local / "app.yaml").write_text("value: 99", encoding="utf-8")
    with pytest.raises(remote.RemoteConfigError, match="已变化"):
        remote.apply_outcome(result)
    assert (system / "opaque.bin").read_bytes() == b"old"
    assert not activated
    result = remote.run_sync(job)
    (local / "diagnostics").mkdir()
    (local / "diagnostics/runtime.log").write_text("task is running", encoding="utf-8")
    assert remote.apply_outcome(result) == ("opaque.bin",)
    assert activated == [("opaque.bin",)]
    seen = []
    assert observer.poll(seen.append) == ("opaque.bin",)
    assert observer.poll(seen.append) == ()
    assert seen == [("opaque.bin",)]
    assert (local / "app.yaml").read_text(encoding="utf-8") == "value: 99"
    # 同修订再次同步不重新解析或切换目录。
    remote.apply_outcome(remote.run_sync(job))
    assert checks == [b"new", b"new"]
    assert activated == [("opaque.bin",)]
