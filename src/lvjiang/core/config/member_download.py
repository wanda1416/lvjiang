"""会员完整包下载与授权验证；失败不撤回已有内容，明确拒绝授权才撤回。"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from loguru import logger
from packaging.version import Version

from ..update_bundle import json_bytes
from .content_service import ServiceError, request_json
from .protected_bundle import MAX_BUNDLE_BYTES, decrypt, digest
from .protected_remote import (
    ProtectedAccess,
    manifest_url,
    member_access_status,
    parse_manifest,
    report_access_status,
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
        logger.debug("[在线配置] 跳过会员更新：未取得有效的本地会员激活码")
        return MemberDownload({}, {})
    report_access_status(access, "checking", "正在检查云端会员授权")
    try:
        return _fetch_member_update(access, app_version, fetch=fetch, timeout=timeout, cache_index=cache_index)
    except Exception as error:
        if member_access_status(access.code_hash).state != "allowed":
            report_access_status(access, "unavailable", "在线检查未完成，请查看配置同步日志并重试")
        logger.debug("[在线配置] 会员更新未完成：{}；保留已知授权结论，不将下载失败误判为授权拒绝", error)
        raise


def _fetch_member_update(access: ProtectedAccess, app_version: str, *,
                         fetch: Callable[..., tuple[bytes, str, bool]], timeout: float,
                         cache_index: Callable[[bytes], None] | None) -> MemberDownload:
    raw, _, _ = fetch(manifest_url(), max_bytes=256 * 1024, timeout=timeout)
    index = parse_manifest(json.loads(raw))
    if cache_index is not None:
        cache_index(raw)
    package = select_package(index, app_version)
    if package is None:
        report_access_status(access, "unchecked", "没有适用当前版本的会员包，尚未验证云端授权")
        logger.debug("[在线配置] 跳过会员更新：没有适用客户端 {} 的会员包", app_version)
        return MemberDownload({}, {"config_version": index["config_version"]}, index_json=raw)
    nonce = secrets.token_hex(16) if access.serial else ""
    from ..license.code import normalize_code
    request = {"code": normalize_code(access.code), "key_id": package["key_id"]}
    if access.serial:
        request.update(serial=access.serial, nonce=nonce)
    try:
        grant = request_json(access.service, "/v1/key", request,
                             timeout=timeout)
    except ServiceError as error:
        if not access.serial and error.status == 403 and error.code == "forbidden":
            reason = {
                "license_unregistered": "激活码尚未在云端登记",
                "license_disabled": "激活码已在云端停用",
                "license_expired": "激活码的云端授权已过期",
                "insufficient_level": "激活码等级不足以获取此会员包",
                "package_unavailable": "会员包不存在或已停用",
                "invalid_request": "会员授权请求无效",
            }.get(error.reason, "云端拒绝授权，可能未登记、已停用、已过期或等级不足")
            report_access_status(access, "denied", reason)
            logger.debug("[在线配置] 跳过会员更新：{}（HTTP 403）；本地激活有效不代表云端授权有效", reason)
            return MemberDownload({}, {}, index_json=raw)
        raise
    expires_at = access.expires_at
    if access.serial:
        from ..license.activation import STATUS_REASON, accept_status
        data = accept_status(access.code, access.serial, grant.get("receipt"), nonce)
        if data["status"] != "active":
            report_access_status(access, "denied", STATUS_REASON[data["status"]])
            return MemberDownload({}, {}, index_json=raw)
        expires_at = min(filter(lambda value: value is not None,
                                (expires_at, data["expires_at"])))
        if data.get("package_key_id") != package["key_id"]:
            raise ValueError("会员包签名回复未关联本次请求的包")
        # Package-specific denial must itself be signed.
        if data.get("package_status") in {"package_unavailable", "insufficient_level"}:
            report_access_status(access, "denied", "云端会员包不可用或等级不足")
            return MemberDownload({}, {}, index_json=raw)
        proof = json.dumps([package["key_id"], grant.get("key"), grant.get("sha256"),
                            grant.get("min_app_version"), grant.get("config_version"), grant.get("expires")],
                           separators=(",", ":")).encode("utf-8")
        if data.get("package_status") != "allowed" or data.get("package_grant_hash") != hashlib.sha256(proof).hexdigest():
            raise ValueError("会员密钥响应不匹配服务器签名")
    if grant.get("sha256") != package["sha256"] or grant.get("config_version") != package["config_version"] \
            or grant.get("min_app_version") != package["min_app_version"]:
        raise ValueError("会员授权响应与公开密文清单不一致")
    expires = min(filter(None, (access.expires, grant.get("expires"))), default=None)
    if (expires is not None and date.fromisoformat(expires) < date.today()) \
            or (expires_at is not None and time.time() >= expires_at):
        report_access_status(access, "denied", "会员授权已过期")
        logger.debug("[在线配置] 跳过会员更新：会员授权已过期")
        return MemberDownload({}, {}, index_json=raw)
    from ..versioning import config_version
    if config_version(app_version) < Version(grant["min_app_version"]):
        report_access_status(access, "unchecked", "应用版本未满足会员包要求")
        logger.debug("[在线配置] 跳过会员更新：应用版本未满足会员包要求")
        return MemberDownload({}, {}, index_json=raw)
    report_access_status(access, "allowed", "最近检查通过")
    logger.debug("[在线配置] 云端会员授权检查通过，开始获取会员包 r{}", package["config_version"])
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
                                  "code_hash": access.code_hash, "expires": expires,
                                  "expires_at": expires_at}, encrypted,
                          json_bytes(package), raw)
