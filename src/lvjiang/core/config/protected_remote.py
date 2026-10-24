"""会员索引与授权契约；归档、合成和安装由统一更新服务负责。"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from loguru import logger
from packaging.version import Version

from .content_service import CONTENT_SERVICE_URL
from .protected_bundle import (
    digest,
    valid_key_id,
)


def manifest_url() -> str:
    from ..update_bundle import MEMBER_INDEX_URL
    return MEMBER_INDEX_URL


@dataclass(frozen=True)
class ProtectedAccess:
    service: str
    code: str = field(repr=False)
    expires: str | None = None
    serial: str = field(default="", repr=False)
    expires_at: int | None = None

    @property
    def code_hash(self) -> str:
        from ..license.code import normalize_code
        return digest(normalize_code(self.code).upper().encode("utf-8"))


@dataclass(frozen=True)
class MemberAccessStatus:
    """本次进程的在线检查结果，不参与离线功能门禁或配置持久化。"""

    code_hash: str = field(default="", repr=False)
    state: str = "unchecked"
    reason: str = "尚未检查，不代表云端授权有效"


_access_status = MemberAccessStatus()


def report_access_status(access: ProtectedAccess, state: str, reason: str) -> None:
    global _access_status
    _access_status = MemberAccessStatus(access.code_hash, state, reason)


def member_access_status(code_hash: str | None = None) -> MemberAccessStatus:
    """换码后不展示上一张码的在线结果；UI 只读缓存，不同步请求网络。"""
    from ..license import load_code
    from ..license.code import normalize_code
    if code_hash is None:
        code = load_code()
        code_hash = digest(normalize_code(code).upper().encode("utf-8")) if code else ""
    current = code_hash
    return _access_status if current and current == _access_status.code_hash else MemberAccessStatus()


def snapshot() -> ProtectedAccess | None:
    from ..license import current_serial, load_code
    from ..license.activation import stored_entitlement
    from ..license.hardware import normalize_serial
    code = load_code()
    if not code:
        logger.debug("[在线配置] 未填写激活码，不检查云端会员授权")
        return None
    serial = current_serial()
    entitlement = stored_entitlement(serial)
    if not eligible(entitlement):
        reason = entitlement.reason if entitlement.license is None else "本地授权未达到会员等级或已过期"
        logger.debug("[在线配置] 不检查云端会员授权：{}", reason)
        return None
    issued = entitlement.license
    return ProtectedAccess(CONTENT_SERVICE_URL, code,
                           issued.expires.isoformat() if issued and issued.expires else None,
                           normalize_serial(serial or "") if issued and issued.bind == "claim" else "",
                           entitlement.expires_at)


def eligible(entitlement) -> bool:
    issued = entitlement.license
    return bool(issued and not issued.expired() and any(
        re.fullmatch(r"lv[1-9]\d*", feature)
        for feature in entitlement.features))


def parse_manifest(manifest: Any) -> dict[str, Any]:
    """校验保护包清单；保留顺序，包版本独立于整份清单修订号。"""
    if not isinstance(manifest, dict):
        raise ValueError("加密清单必须是对象")
    version = manifest.get("config_version")
    packages = manifest.get("packages")
    if manifest.get("schema_version") != 1 or type(version) is not int or version < 0 \
            or not isinstance(packages, list):
        raise ValueError("加密清单协议无效")
    normalized = []
    ids = set()
    for package in packages:
        if not isinstance(package, dict):
            raise ValueError("加密清单包定义无效")
        key_id = package.get("key_id", "")
        if not valid_key_id(key_id) or key_id in ids:
            raise ValueError("加密清单 key_id 无效或重复")
        ids.add(key_id)
        if not isinstance(package.get("url"), str) or not package["url"]:
            raise ValueError("加密包地址无效")
        if not isinstance(package.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", package["sha256"]):
            raise ValueError("加密包摘要无效")
        minimum = package.get("min_app_version", "0.0.0")
        maximum = package.get("max_app_version_exclusive", "")
        if not isinstance(minimum, str) or not isinstance(maximum, str):
            raise ValueError("加密包客户端版本范围无效")
        from ..update_bundle import series
        if series(minimum) != minimum or maximum and series(maximum) != maximum:
            raise ValueError("加密包客户端版本范围只允许三段版本号")
        if maximum and Version(minimum) >= Version(maximum):
            raise ValueError("加密包客户端版本范围为空")
        Version(minimum)
        package_version = package.get("config_version", version)
        if type(package_version) is not int or package_version < 1:
            raise ValueError("加密包修订号无效")
        normalized.append({**package, "min_app_version": minimum,
                           "max_app_version_exclusive": maximum, "config_version": package_version})
    return {**manifest, "packages": normalized}


def select_package(manifest: dict[str, Any], app_version: str) -> dict[str, Any] | None:
    """从已校验清单中取第一个匹配当前客户端版本的包。"""
    from ..versioning import config_version
    current = config_version(app_version)
    for package in manifest["packages"]:
        if current < Version(package["min_app_version"]):
            continue
        maximum = package["max_app_version_exclusive"]
        if not maximum or current < Version(maximum):
            return package
    return None


def authorized(doc: dict[str, Any]) -> bool:
    from ..license import current_entitlement, load_code
    from ..license.code import normalize_code
    entitlement = current_entitlement()
    if not eligible(entitlement):
        return False
    expires_at = doc.get("expires_at")
    if expires_at is not None and (type(expires_at) is not int or time.time() >= expires_at):
        return False
    expires = doc.get("expires")
    if expires is not None:
        if not isinstance(expires, str):
            return False
        try:
            if date.fromisoformat(expires) < date.today():
                return False
        except ValueError:
            return False
    return digest(normalize_code(load_code() or "").upper().encode("utf-8")) == doc.get("code_hash")
