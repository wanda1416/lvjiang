"""保护双来源同步、授权隔离与包验证的持续运行契约。"""
from __future__ import annotations

import base64
import io
import json
import os
import zipfile
from datetime import date, timedelta

import pytest

from lvjiang.core.config import protected_remote as protected
from lvjiang.core.config import remote
from lvjiang.core.config.content_service import ServiceError
from lvjiang.core.config.protected_bundle import digest, encrypt, unpack
from lvjiang.core.config.resolver import ConfigResolver
from lvjiang.core.license.code import Entitlement, License


@pytest.fixture
def feed(tmp_path, monkeypatch):
    root = tmp_path / "remote"
    access = protected.ProtectedAccess("https://service.example.invalid", "test-only-code")
    key = os.urandom(32)
    files = {"workflows/weekly_test.wf": b'log "first"\n'}
    package = encrypt(files, key, os.urandom(12))
    sha = digest(package)
    bundle_url = protected.manifest_url().rsplit("/", 1)[0] + "/packages/test-package.bin"
    manifest = {"schema_version": 1, "config_version": 1, "packages": [{
        "key_id": "test-package", "url": bundle_url, "sha256": sha}]}
    urls = {protected.manifest_url(): json.dumps(manifest).encode("utf-8"), bundle_url: package}
    grant = {"key": base64.b64encode(key).decode("ascii"), "sha256": sha,
             "min_app_version": "1.0.0", "config_version": 1, "expires": None}
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        return urls[url], "", False
    monkeypatch.setattr(remote, "_fetch_bytes", fetch)
    monkeypatch.setattr(protected, "request_json", lambda *args, **kwargs: grant)
    return root, access, key, urls, grant, calls


def test_public_304_still_updates_protected_workflow_and_public_sync_preserves_it(feed, monkeypatch):
    root, access, key, urls, grant, calls = feed
    public = remote.RemoteEntry("workflows/public.wf", "https://site.example.invalid/public.wf", digest(b'log "public"\n'), 0)
    manifest = remote.RemoteManifest(1, 1, "", (public,))
    monkeypatch.setattr(remote, "fetch_manifest", lambda **kwargs: (manifest, "public-etag"))
    urls[public.url] = b'log "public"\n'
    job = remote.SyncJob(app_version="1.0.0", protected=access)
    assert set(remote.run_sync(job, remote_dir=root).updated) == {public.rel_path, "workflows/weekly_test.wf"}
    metadata = protected.state(root)
    source = json.loads(urls[protected.manifest_url()])
    source["config_version"] = grant["config_version"] = 2
    replacement = encrypt({"workflows/weekly_test.wf": b'log "updated"\n'}, key, os.urandom(12))
    source["packages"][0]["sha256"] = grant["sha256"] = digest(replacement)
    urls[source["packages"][0]["url"]] = replacement
    urls[protected.manifest_url()] = json.dumps(source).encode("utf-8")
    monkeypatch.setattr(remote, "fetch_manifest", lambda **kwargs: (None, "public-etag"))
    result = remote.run_sync(job, remote_dir=root)
    assert result.not_modified and result.changed
    assert (root / public.rel_path).is_file()
    assert (root / "workflows/weekly_test.wf").read_bytes() == b'log "updated"\n'
    assert protected.state(root)["config_version"] > metadata["config_version"]


def test_revocation_removes_only_owned_files_and_restores_overlapping_public_path(feed, monkeypatch):
    root, access, _, urls, _, calls = feed
    protected.sync(access, root, "1.0.0", 1)
    public_payload = b'log "public restoration"\n'
    path = "workflows/weekly_test.wf"
    entry = remote.RemoteEntry(path, "https://site.example.invalid/recovery.wf", digest(public_payload), 0)
    urls[entry.url] = public_payload
    public_manifest = remote.RemoteManifest(1, 3, "", (entry,))
    etags = []
    def public_fetch(**kwargs):
        etags.append(kwargs["etag"])
        return public_manifest, "new"
    monkeypatch.setattr(remote, "fetch_manifest", public_fetch)
    def denied(*args, **kwargs):
        raise ServiceError("forbidden", 403, code="forbidden")
    monkeypatch.setattr(protected, "request_json", denied)
    result = remote.run_sync(remote.SyncJob(app_version="1.0.0", protected=access, etag="old"), remote_dir=root)
    assert etags == [""]
    assert path in result.removed and path in result.updated
    assert (root / path).read_bytes() == public_payload
    assert not protected.owned(root)


def test_no_authorization_makes_no_protected_requests(feed, monkeypatch):
    root, _, _, _, _, calls = feed
    monkeypatch.setattr(remote, "fetch_manifest", lambda **kwargs: (None, "public-etag"))
    remote.run_sync(remote.SyncJob(app_version="1.0.0"), remote_dir=root)
    assert calls == []


def test_expired_or_changed_license_hides_remote_but_keeps_local_override(feed, monkeypatch):
    from lvjiang.core import license as licensing
    root, access, _, _, _, _ = feed
    protected.sync(access, root, "1.0.0", 1)
    tomorrow = date.today() + timedelta(days=1)
    issued = License(1, "test", "none", None, ("lv1",), tomorrow)
    monkeypatch.setattr(licensing, "current_entitlement", lambda: Entitlement(issued, features=frozenset({"lv1"})))
    monkeypatch.setattr(licensing, "load_code", lambda: access.code)
    resolver = ConfigResolver(root.parent / "system", root.parent / "local", remote_dir=root)
    path = "workflows/weekly_test.wf"
    assert resolver.resolve_read(path) == root / path
    monkeypatch.setattr(licensing, "load_code", lambda: "different-code")
    assert resolver.resolve_read(path) is None
    local = root.parent / "local" / path
    local.parent.mkdir(parents=True)
    local.write_text('log "local"\n', encoding="utf-8")
    assert resolver.resolve_read(path) == local
    expired = License(1, "test", "none", None, ("lv1",), date.today() - timedelta(days=1))
    monkeypatch.setattr(licensing, "current_entitlement", lambda: Entitlement(expired, features=frozenset({"lv1"})))
    assert protected.purge_invalid(root)
    assert local.is_file()


def test_package_authentication_and_unsafe_zip_leave_existing_files_untouched(feed):
    root, access, key, urls, grant, _ = feed
    protected.sync(access, root, "1.0.0", 1)
    before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    source = json.loads(urls[protected.manifest_url()])
    payload = bytearray(urls[source["packages"][0]["url"]])
    payload[-1] ^= 1
    source["packages"][0]["sha256"] = grant["sha256"] = digest(payload)
    urls[protected.manifest_url()] = json.dumps(source).encode("utf-8")
    urls[source["packages"][0]["url"]] = bytes(payload)
    with pytest.raises(ValueError, match="认证"):
        protected.sync(access, root, "1.0.0", 1)
    assert before == {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("workflows/../../escape.wf", b'log "unsafe"')
    with pytest.raises(ValueError, match="路径"):
        unpack(stream.getvalue())


def test_directory_commit_failure_restores_complete_previous_bundle(feed, monkeypatch):
    root, access, _, _, _, _ = feed
    protected.sync(access, root, "1.0.0", 1)
    old = (root / "workflows/weekly_test.wf").read_bytes()
    original = protected.atomic_write_bytes
    def fail(path, data, **kwargs):
        if path.name == protected.STATE_FILE:
            raise OSError("test disk failure")
        original(path, data, **kwargs)
    monkeypatch.setattr(protected, "atomic_write_bytes", fail)
    with pytest.raises(OSError):
        protected.commit_bundle(root, {"workflows/weekly_test.wf": b'log "new"'}, {}, [])
    assert (root / "workflows/weekly_test.wf").read_bytes() == old
    assert protected.state(root)["files"]


def test_revocation_public_network_failure_clears_etag_so_restoration_is_retried(feed, monkeypatch):
    root, access, _, urls, _, _ = feed
    protected.sync(access, root, "1.0.0", 1)
    def denied(*args, **kwargs):
        raise ServiceError("forbidden", 403, code="forbidden")
    def offline(**kwargs):
        raise remote.RemoteConfigError("test public offline")
    monkeypatch.setattr(protected, "request_json", denied)
    monkeypatch.setattr(remote, "fetch_manifest", offline)
    first = remote.run_sync(remote.SyncJob(app_version="1.0.0", protected=access,
        etag="old-public", config_version=4), remote_dir=root)
    assert first.removed and not first.not_modified and first.etag == ""
    assert first.config_version == 4
    payload = b'log "public"\n'
    entry = remote.RemoteEntry("workflows/weekly_test.wf", "https://site.example.invalid/public.wf", digest(payload), 0)
    urls[entry.url] = payload
    requested = []
    def restored(**kwargs):
        requested.append(kwargs["etag"])
        return remote.RemoteManifest(1, 4, "", (entry,)), "new-public"
    monkeypatch.setattr(remote, "fetch_manifest", restored)
    second = remote.run_sync(remote.SyncJob(app_version="1.0.0", protected=access,
        etag=first.etag, config_version=first.config_version), remote_dir=root)
    assert requested == [""] and entry.rel_path in second.updated
    assert (root / entry.rel_path).read_bytes() == payload


def test_owned_cleanup_does_not_require_plugin_policies_during_early_startup(tmp_path):
    from lvjiang.core.config import versioning
    name = "unloaded_addon/models/model.yaml"
    assert versioning.spec_for(name) is None
    payload = tmp_path / name
    payload.parent.mkdir(parents=True)
    payload.write_text("content_version: 1", encoding="utf-8")
    (tmp_path / protected.STATE_FILE).write_text(json.dumps({"files": {name: digest(payload.read_bytes())}}), encoding="utf-8")
    assert protected.remove_owned(tmp_path) == (name,)
    assert not payload.exists()


def test_edge_403_does_not_withdraw_valid_downloaded_content(feed, monkeypatch):
    root, access, _, _, _, _ = feed
    protected.sync(access, root, "1.0.0", 1)
    def edge_block(*args, **kwargs):
        raise ServiceError("edge rejection", 403)
    monkeypatch.setattr(protected, "request_json", edge_block)
    monkeypatch.setattr(remote, "fetch_manifest", lambda **kwargs: (None, "public-etag"))
    result = remote.run_sync(remote.SyncJob(app_version="1.0.0", protected=access), remote_dir=root)
    assert not result.removed
    assert (root / "workflows/weekly_test.wf").is_file()
