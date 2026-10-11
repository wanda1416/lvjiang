"""会员完整包下载与授权验证；失败不撤回已有内容，明确拒绝授权才撤回。"""
from __future__ import annotations

import base64
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from packaging.version import Version

from ..update_bundle import json_bytes
from .content_service import ServiceError, request_json
from .protected_bundle import MAX_BUNDLE_BYTES, decrypt, digest
from .protected_remote import (
    ProtectedAccess,
    manifest_url,
    parse_manifest,
    select_package,
)


@dataclass(frozen=True)
class MemberDownload:
    files: dict[str, bytes]
    metadata: dict[str, Any]
    encrypted: bytes = b""
    manifest_json: bytes = b""
    index_json: bytes = b""


def fetch_member_update(access: ProtectedAccess | None, app_version: str, *,
                        fetch: Callable[..., tuple[bytes, str, bool]], timeout: float = 10,
                        cache_index: Callable[[bytes], None] | None = None) -> MemberDownload:
    if access is None:
        return MemberDownload({}, {})
    raw, _, _ = fetch(manifest_url(), max_bytes=256 * 1024, timeout=timeout)
    index = parse_manifest(json.loads(raw))
    if cache_index is not None:
        cache_index(raw)
    package = select_package(index, app_version)
    if package is None:
        return MemberDownload({}, {"config_version": index["config_version"]}, index_json=raw)
    try:
        grant = request_json(access.service, "/v1/key", {"code": access.code, "key_id": package["key_id"]},
                             timeout=timeout)
    except ServiceError as error:
        if error.status == 403 and error.code == "forbidden":
            return MemberDownload({}, {}, index_json=raw)
        raise
    if grant.get("sha256") != package["sha256"] or grant.get("config_version") != package["config_version"] \
            or grant.get("min_app_version") != package["min_app_version"]:
        raise ValueError("会员授权响应与公开密文清单不一致")
    expires = min(filter(None, (access.expires, grant.get("expires"))), default=None)
    if expires is not None and date.fromisoformat(expires) < date.today():
        return MemberDownload({}, {}, index_json=raw)
    from ..versioning import config_version
    if config_version(app_version) < Version(grant["min_app_version"]):
        return MemberDownload({}, {}, index_json=raw)
    encrypted, _, _ = fetch(package["url"], max_bytes=MAX_BUNDLE_BYTES, timeout=timeout)
    if digest(encrypted) != package["sha256"]:
        raise ValueError("会员密文包摘要不匹配")
    files = decrypt(encrypted, base64.b64decode(grant["key"], validate=True))
    return MemberDownload(files, {"key_id": package["key_id"], "sha256": package["sha256"],
                                  "config_version": index["config_version"],
                                  "package_version": package["config_version"],
                                  "min_app_version": package["min_app_version"],
                                  "max_app_version_exclusive": package["max_app_version_exclusive"],
                                  "public_revisions": package["public_revisions"],
                                  "public_manifests": package.get("public_manifests", {}),
                                  "manifest_sha256": digest(json_bytes(package)),
                                  "code_hash": access.code_hash, "expires": expires}, encrypted,
                          json_bytes(package), raw)
