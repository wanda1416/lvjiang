"""会员传输的持续边界：拒绝混包，区分断网与撤权，保留未知安全文件。"""
import base64

import pytest

from lvjiang.core.config import member_download, protected_remote
from lvjiang.core.config.content_service import ServiceError
from lvjiang.core.config.protected_bundle import digest, encrypt
from lvjiang.core.config.protected_remote import (
    ProtectedAccess,
    manifest_url,
    parse_manifest,
)
from lvjiang.core.update_bundle import json_bytes


def test_member_integrity_and_authorization_failure_semantics(monkeypatch):
    monkeypatch.setattr(protected_remote, "_access_status", protected_remote.MemberAccessStatus())
    key = b"k" * 32
    contents = {"future/opaque.bin": b"\xff\x00"}
    encrypted = encrypt(contents, key, b"n" * 12)
    package = {"key_id": "test-package", "sha256": digest(encrypted),
               "config_version": 3, "min_app_version": "0.15.0",
               "url": manifest_url().rsplit("/", 1)[0] + "/packages/test-package.bin"}
    index = json_bytes({"schema_version": 1, "config_version": 3, "packages": [package]})
    grant = {**package, "key": base64.b64encode(key).decode("ascii")}
    monkeypatch.setattr(member_download, "request_json", lambda *a, **kw: grant)
    access = ProtectedAccess("https://example.invalid", "synthetic-license")
    def fetch(url, **kwargs):
        return (index if url == manifest_url() else encrypted), "", False
    result = member_download.fetch_member_update(access, "0.15.0b1", fetch=fetch)
    assert result.files == contents
    assert result.encrypted == encrypted
    assert protected_remote.member_access_status(access.code_hash).state == "allowed"
    def damaged(url, **kwargs):
        return (index if url == manifest_url() else encrypted[:-1]), "", False
    with pytest.raises(ValueError, match="摘要不匹配"):
        member_download.fetch_member_update(access, "0.15.0b1", fetch=damaged)
    assert protected_remote.member_access_status(access.code_hash).state == "allowed"
    with pytest.raises(ValueError, match="三段版本号"):
        parse_manifest({"schema_version": 1, "config_version": 3,
                        "packages": [{**package, "min_app_version": "0.15.0rc1"}]})
    grant["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="不一致"):
        member_download.fetch_member_update(access, "0.15.0", fetch=fetch)
    def offline(*args, **kwargs):
        raise ServiceError("unavailable", 503)
    monkeypatch.setattr(member_download, "request_json", offline)
    with pytest.raises(ServiceError):
        member_download.fetch_member_update(access, "0.15.0", fetch=fetch)
    assert protected_remote.member_access_status(access.code_hash).state == "unavailable"
    def forbidden(*args, **kwargs):
        raise ServiceError("denied", 403, code="forbidden", reason="license_disabled")
    monkeypatch.setattr(member_download, "request_json", forbidden)
    from loguru import logger
    logs = []
    sink = logger.add(lambda message: logs.append(str(message)), level="DEBUG")
    try:
        assert member_download.fetch_member_update(access, "0.15.0", fetch=fetch).files == {}
    finally:
        logger.remove(sink)
    status = protected_remote.member_access_status(access.code_hash)
    assert status.state == "denied" and "云端停用" in status.reason
    assert "云端停用" in "".join(logs) and access.code not in "".join(logs)


def test_member_authenticated_archive_rejects_tamper_and_hierarchy_collision():
    from lvjiang.core.config.protected_bundle import decrypt
    key = b"k" * 32
    with pytest.raises(ValueError, match="冲突"):
        encrypt({"a": b"x", "a/b": b"y"}, key, b"n" * 12)
    encrypted = encrypt({"x": b"x"}, key, b"n" * 12)
    with pytest.raises(ValueError, match="认证失败"):
        decrypt(encrypted[:-1] + bytes([encrypted[-1] ^ 1]), key)
