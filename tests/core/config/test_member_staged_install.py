"""会员包的「后台只下密文、下次启动再解密安装」链路。

这里把 HTTP 与授权接口打桩在模块边界（`remote._fetch_bytes`、`member_download.request_json`），
其余全部走真实实现：真实 `UpdateStore` 暂存与 prune、真实 `install_staged_member` 的
摘要校验与解密、真实 `apply_outcome` 的授权复核。

钉住的契约：
1. 后台只暂存密文，磁盘上不出现会员明文；
2. 下次启动安装时**不重新下载 package.bin**，只为解密取一次授权；
3. 授权请求失败时保留已装内容与候选，下一次还能装上；
4. 授权被拒时清掉候选，不装也不删已装内容；
5. 暂存清单被改坏时拒绝安装并保留候选，不静默装错包。
"""
import base64
from types import SimpleNamespace

from lvjiang.core.config import member_download, protected_remote, remote
from lvjiang.core.config import resolver as resolver_module
from lvjiang.core.config.content_service import ServiceError
from lvjiang.core.config.protected_bundle import digest, encrypt
from lvjiang.core.config.protected_remote import ProtectedAccess, manifest_url
from lvjiang.core.config.resolver import ConfigResolver
from lvjiang.core.system_preset import write_baseline_identity
from lvjiang.core.update_bundle import json_bytes

KEY = b"k" * 32
CODE = "synthetic-member-code"
CONTENTS = {"member.bin": b"member-payload"}


def _release_resolver(tmp_path, monkeypatch):
    system = tmp_path / "system"
    system.mkdir()
    (system / "opaque.bin").write_bytes(b"old")
    resolver = ConfigResolver(system, tmp_path / "local", dev_mode=False, config_dir=tmp_path)
    monkeypatch.setattr(resolver_module, "_resolver", resolver)
    monkeypatch.setattr("lvjiang.core.update.get_version", lambda: "0.15.0")
    write_baseline_identity(system, "0.15.0", config_dir=tmp_path)
    monkeypatch.setattr(remote, "is_enabled", lambda: True)
    monkeypatch.setattr(remote, "fetch_public_update", lambda *_a, **_k: None)
    access = ProtectedAccess("https://example.invalid", CODE)
    monkeypatch.setattr(protected_remote, "snapshot", lambda: access)
    # 安装阶段的授权复核读的是本机激活状态，不是下载时的那份快照。
    monkeypatch.setattr("lvjiang.core.license.load_code", lambda: CODE)
    monkeypatch.setattr(
        "lvjiang.core.license.current_entitlement",
        lambda: SimpleNamespace(
            features=["lv1"],
            license=SimpleNamespace(expired=lambda: False, expires=None),
            expires_at=None, reason=""),
    )
    monkeypatch.setattr(protected_remote, "_access_status", protected_remote.MemberAccessStatus())
    return system


class _Harness:
    """包线上环境：索引、密文包与授权接口，并记录每次请求。"""

    def __init__(self, monkeypatch):
        self.encrypted = encrypt(CONTENTS, KEY, b"n" * 12)
        self.package = {
            "key_id": "test-package",
            "sha256": digest(self.encrypted),
            "config_version": 5,
            "package_version": 7,
            "min_app_version": "0.15.0",
            "max_app_version_exclusive": "0.16.0",
            "url": manifest_url().rsplit("/", 1)[0] + "/packages/test-package.bin",
        }
        self.index = json_bytes(
            {"schema_version": 1, "config_version": 5, "packages": [self.package]})
        self.fetches: list[str] = []
        self.grants = 0
        self.grant_error: Exception | None = None
        monkeypatch.setattr(remote, "_fetch_bytes", self._fetch)
        monkeypatch.setattr(member_download, "request_json", self._request_json)

    def _fetch(self, url, *, max_bytes, timeout, etag="", cancelled=None):
        self.fetches.append(url)
        if url == manifest_url():
            return self.index, "", False
        if url == self.package["url"]:
            return self.encrypted, "", False
        raise AssertionError(f"未预期的下载地址: {url}")

    def _request_json(self, _service, _path, _body, *, timeout):
        self.grants += 1
        if self.grant_error is not None:
            raise self.grant_error
        return {**self.package, "key": base64.b64encode(KEY).decode("ascii"),
                "expires": "2099-01-01"}

    def reset(self):
        self.fetches.clear()
        self.grants = 0


def _stage(tmp_path, monkeypatch, system):
    harness = _Harness(monkeypatch)
    remote.stage_outcome(remote.run_sync(remote.build_sync_job()))
    assert harness.fetches == [manifest_url(), harness.package["url"]]
    assert harness.grants == 1
    # 后台阶段只暂存密文：生效目录与暂存区都不应出现会员明文。
    assert not (system / "member.bin").exists()
    staged = [
        path for path in (tmp_path / "updates").rglob("*")
        if path.is_file() and path.name in CONTENTS
    ]
    assert staged == []
    harness.reset()
    return harness


def test_staged_member_installs_without_downloading_package_again(tmp_path, monkeypatch):
    system = _release_resolver(tmp_path, monkeypatch)
    harness = _stage(tmp_path, monkeypatch, system)

    remote.prepare_startup_configuration()

    assert (system / "member.bin").read_bytes() == CONTENTS["member.bin"]
    # 只为解密取一次授权，整包不再下载（索引也不用再取）。
    assert harness.grants == 1
    assert harness.fetches == []
    store = remote._installed_store()
    assert store is not None and store.pending_member() is None


def test_offline_startup_keeps_candidate_and_installs_on_retry(tmp_path, monkeypatch):
    system = _release_resolver(tmp_path, monkeypatch)
    harness = _stage(tmp_path, monkeypatch, system)

    harness.grant_error = ServiceError("unavailable", 503)
    remote.prepare_startup_configuration()
    assert not (system / "member.bin").exists()
    store = remote._installed_store()
    assert store is not None and store.pending_member() is not None

    harness.grant_error = None
    remote.prepare_startup_configuration()
    assert (system / "member.bin").read_bytes() == CONTENTS["member.bin"]
    assert store.pending_member() is None


def test_denied_authorization_clears_candidate_without_installing(tmp_path, monkeypatch):
    system = _release_resolver(tmp_path, monkeypatch)
    harness = _stage(tmp_path, monkeypatch, system)

    harness.grant_error = ServiceError(
        "denied", 403, code="forbidden", reason="license_disabled")
    remote.prepare_startup_configuration()

    assert not (system / "member.bin").exists()
    store = remote._installed_store()
    assert store is not None and store.pending_member() is None


def test_tampered_staged_manifest_is_rejected_and_keeps_candidate(tmp_path, monkeypatch):
    system = _release_resolver(tmp_path, monkeypatch)
    harness = _stage(tmp_path, monkeypatch, system)

    store = remote._installed_store()
    assert store is not None
    for manifest in (store.root / "member").rglob("manifest.json"):
        manifest.write_bytes(json_bytes({**harness.package, "key_id": "other-package"}))

    remote.prepare_startup_configuration()
    assert not (system / "member.bin").exists()
    assert store.pending_member() is not None


def test_expired_entitlement_does_not_install_staged_member(tmp_path, monkeypatch):
    """暂存里有密文，但本机授权已过期：不得装进 system。"""
    system = _release_resolver(tmp_path, monkeypatch)
    _stage(tmp_path, monkeypatch, system)
    monkeypatch.setattr(
        "lvjiang.core.license.current_entitlement",
        lambda: SimpleNamespace(
            features=["lv1"],
            license=SimpleNamespace(expired=lambda: True, expires=None),
            expires_at=None, reason=""),
    )
    remote.prepare_startup_configuration()
    assert not (system / "member.bin").exists()
