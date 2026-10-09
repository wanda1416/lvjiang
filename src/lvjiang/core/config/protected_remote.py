"""受保护来源同步到同一 remote 暂存层，沿用现有加载器。"""
from __future__ import annotations

import base64
import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from packaging.version import Version

from ..fs_util import atomic_write_bytes
from .content_service import ServiceError, request_json, service_url
from .protected_bundle import (
    MAX_BUNDLE_BYTES,
    decrypt,
    digest,
    safe_path,
    valid_key_id,
)


def manifest_url() -> str:
    from .remote import REMOTE_CONFIG_URL
    return REMOTE_CONFIG_URL.rsplit("/config/", 1)[0] + "/protected/config.json"


STATE_FILE = ".protected.json"


@dataclass(frozen=True)
class ProtectedAccess:
    service: str
    code: str = field(repr=False)
    expires: str | None = None

    @property
    def code_hash(self) -> str:
        from ..license.code import normalize_code
        return digest(normalize_code(self.code).upper().encode("utf-8"))


def snapshot() -> ProtectedAccess | None:
    from ..license import current_serial, evaluate, load_code
    from .resolver import LOCAL_CONFIG_DIR
    code = load_code()
    if not code:
        return None
    entitlement = evaluate(code, current_serial())
    if not eligible(entitlement):
        return None
    settings = LOCAL_CONFIG_DIR / "remote_service.json"
    if not settings.is_file():
        return None
    config = json.loads(settings.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not isinstance(config.get("service_url"), str):
        raise ValueError("remote_service.json 缺少 service_url")
    url = service_url(config["service_url"])
    issued = entitlement.license
    return ProtectedAccess(url, code, issued.expires.isoformat() if issued and issued.expires else None)


def eligible(entitlement) -> bool:
    issued = entitlement.license
    return bool(issued and not issued.expired() and any(
        re.fullmatch(r"lv[1-9]\d*", feature)
        for feature in entitlement.features))


def state(root: Path) -> dict[str, Any]:
    path = root / STATE_FILE
    if not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or not isinstance(doc.get("files"), dict):
            return {}
        return doc
    except (ValueError, OSError):
        return {}


def owned(root: Path) -> set[str]:
    return {name for name in state(root).get("files", {}) if safe_path(name)}


def authorized(doc: dict[str, Any]) -> bool:
    from ..license import current_entitlement, load_code
    from ..license.code import normalize_code
    entitlement = current_entitlement()
    if not eligible(entitlement):
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


def path_status(root: Path, name: str) -> bool | None:
    """None 为公开来源；False 为失效受保护文件，True 为已授权文件。"""
    doc = state(root)
    if name not in doc.get("files", {}):
        return None
    return authorized(doc)


def remove_owned(root: Path) -> tuple[str, ...]:
    removed = sorted(owned(root))
    for name in removed:
        (root / name).unlink(missing_ok=True)
    (root / STATE_FILE).unlink(missing_ok=True)
    return tuple(removed)


def purge_invalid(root: Path) -> bool:
    doc = state(root)
    if doc and not authorized(doc):
        remove_owned(root)
        return True
    return False


def sync(access: ProtectedAccess | None, root: Path, app_version: str,
         timeout: float) -> tuple[tuple[str, ...], tuple[str, ...]]:
    from .remote import MAX_MANIFEST_BYTES, _fetch_bytes
    recover(root)
    previous = state(root)
    if access is None:
        return (), remove_owned(root)
    if previous and previous.get("code_hash") != access.code_hash:
        remove_owned(root)
        previous = {}
    raw, _, _ = _fetch_bytes(manifest_url(), max_bytes=MAX_MANIFEST_BYTES, timeout=timeout)
    manifest = json.loads(raw.decode("utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("加密清单必须是对象")
    version = manifest.get("config_version")
    packages = manifest.get("packages")
    if manifest.get("schema_version") != 1 or type(version) is not int or version < 1 \
            or not isinstance(packages, list) or len(packages) > 1:
        raise ValueError("加密清单协议无效")
    if version < previous.get("config_version", 0):
        raise ValueError("加密清单版本回退")
    if not packages:
        withdrawn = remove_owned(root)
        empty = {"config_version": version, "files": {}, "code_hash": access.code_hash,
                 "expires": access.expires}
        atomic_write_bytes(root / STATE_FILE, json.dumps(empty).encode("utf-8"), prefix=".protected-")
        return (), withdrawn
    package = packages[0]
    if not isinstance(package, dict):
        raise ValueError("加密清单包定义无效")
    key_id = package.get("key_id", "")
    if not valid_key_id(key_id):
        raise ValueError("加密清单 key_id 无效")
    expected_url = manifest_url().rsplit("/", 1)[0] + f"/packages/{key_id}.bin"
    if package.get("url") != expected_url:
        raise ValueError("加密包地址无效")
    try:
        grant = request_json(access.service, "/v1/key", {"code": access.code, "key_id": key_id}, timeout=timeout)
    except ServiceError as error:
        if error.status == 403 and error.code == "forbidden":
            return (), remove_owned(root)
        raise
    if type(grant.get("config_version")) is not int or grant["config_version"] != version:
        raise ValueError("加密清单与云端包版本不一致")
    if not isinstance(grant.get("min_app_version"), str) or not isinstance(grant.get("key"), str) \
            or not isinstance(grant.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", grant["sha256"]):
        raise ValueError("云端密钥响应格式无效")
    expiry = grant.get("expires")
    if expiry is not None:
        if not isinstance(expiry, str) or date.fromisoformat(expiry).isoformat() != expiry:
            raise ValueError("云端密钥有效期无效")
        if expiry < date.today().isoformat():
            return (), remove_owned(root)
    if Version(app_version) < Version(grant["min_app_version"]):
        return (), remove_owned(root)
    sha = grant["sha256"]
    if package.get("sha256") != sha:
        raise ValueError("加密清单与云端包摘要不一致")
    files: dict[str, bytes]
    unchanged = previous.get("sha256") == sha and all(
        (root / name).is_file() and digest((root / name).read_bytes()) == expected
        for name, expected in previous.get("files", {}).items())
    expires = min(filter(None, (access.expires, grant.get("expires"))), default=None)
    if unchanged:
        files = {name: (root / name).read_bytes() for name in owned(root)}
    else:
        encrypted, _, _ = _fetch_bytes(expected_url, max_bytes=MAX_BUNDLE_BYTES, timeout=timeout)
        if digest(encrypted) != sha:
            raise ValueError("加密包摘要不匹配")
        files = decrypt(encrypted, base64.b64decode(grant["key"], validate=True))
    # 所有文件完成验证后才修改暂存层。启动前提升目录，运行中不热切换。
    removed = sorted(owned(root) - files.keys())
    metadata = {"config_version": version, "code_hash": access.code_hash,
                "expires": expires, "sha256": sha,
                "files": {name: digest(data) for name, data in files.items()}}
    if unchanged:
        atomic_write_bytes(root / STATE_FILE, json.dumps(metadata, ensure_ascii=False).encode("utf-8"), prefix=".protected-")
    else:
        commit_bundle(root, files, metadata, removed)
    return (() if unchanged else tuple(files)), tuple(removed)


def recover(root: Path) -> None:
    """恢复目录替换中断；只处理同步器自己拥有的备份。"""
    retired = root.with_name(root.name + ".protected-old")
    if retired.is_dir():
        if not root.exists():
            retired.rename(root)
        else:
            shutil.rmtree(retired)


def commit_bundle(root: Path, files: dict[str, bytes], metadata: dict[str, Any],
                  removed: list[str]) -> None:
    root.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(dir=root.parent, prefix=root.name + ".bundle-"))
    retired = root.with_name(root.name + ".protected-old")
    try:
        if root.is_dir():
            shutil.copytree(root, temporary, dirs_exist_ok=True)
        for name, data in files.items():
            atomic_write_bytes(temporary / name, data, prefix=".protected-")
        for name in removed:
            (temporary / name).unlink(missing_ok=True)
        atomic_write_bytes(temporary / STATE_FILE,
                           json.dumps(metadata, ensure_ascii=False).encode("utf-8"), prefix=".protected-")
        if root.exists():
            root.rename(retired)
        try:
            temporary.rename(root)
        except OSError:
            if retired.exists():
                retired.rename(root)
            raise
        shutil.rmtree(retired, ignore_errors=True)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
