"""Admin 与客户端共享的加密压缩包协议；不包含授权或发布凭据。"""
from __future__ import annotations

import hashlib
import io
import re
import stat
import zipfile
from pathlib import PurePosixPath

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import versioning

AAD = b"lvjiang-protected-bundle-v1"
MAX_BUNDLE_BYTES = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_MEMBER_BYTES = 4 * 1024 * 1024
MAX_MEMBERS = 2000
REGISTRIES = frozenset({"scenes.yaml", "layouts.yaml"})


def is_asset(name: str) -> bool:
    parts = PurePosixPath(name).parts
    return bool(parts) and ((len(parts) == 3 and parts[0] == "maps" and parts[-1] == "base.png")
                            or (len(parts) >= 3 and parts[0] == "templates"
                                and parts[-1].lower().endswith((".png", ".jpg", ".webp"))))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_path(name: str) -> bool:
    parts = PurePosixPath(name).parts
    return bool(parts) and not name.startswith("/") and "\\" not in name and ":" not in name \
        and all(part and part not in {".", ".."} and not part.startswith(".")
                and not part.endswith((".", " ")) and not re.search(r'[<>"|?*\x00]', part)
                and not re.fullmatch(r"(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])", part.split(".")[0])
                for part in name.split("/"))


def valid_path(name: str) -> bool:
    return safe_path(name) and (name in REGISTRIES or is_asset(name) or versioning.spec_for(name) is not None)


def valid_key_id(value: str) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", value))


def unpack(payload: bytes) -> dict[str, bytes]:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = archive.infolist()
            if not members or len(members) > MAX_MEMBERS:
                raise ValueError("包文件数量无效")
            result: dict[str, bytes] = {}
            total = 0
            seen: set[str] = set()
            for member in members:
                if not valid_path(member.filename) or member.filename.casefold() in seen:
                    raise ValueError("包包含不支持、重复或不安全的路径")
                if stat.S_ISLNK(member.external_attr >> 16):
                    raise ValueError("包不能包含符号链接")
                total += member.file_size
                if member.file_size > MAX_MEMBER_BYTES or total > MAX_EXPANDED_BYTES:
                    raise ValueError("包解压大小超限")
                data = archive.read(member)
                if not is_asset(member.filename):
                    data.decode("utf-8")
                seen.add(member.filename.casefold())
                result[member.filename] = data
            return result
    except (zipfile.BadZipFile, UnicodeDecodeError, RuntimeError) as error:
        raise ValueError("压缩包内容无效") from error


def encrypt(files: dict[str, bytes], key: bytes, nonce: bytes) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
    plain = stream.getvalue()
    unpack(plain)
    result = nonce + AESGCM(key).encrypt(nonce, plain, AAD)
    if len(result) > MAX_BUNDLE_BYTES:
        raise ValueError("加密包大小超限")
    return result


def decrypt(payload: bytes, key: bytes) -> dict[str, bytes]:
    from cryptography.exceptions import InvalidTag
    if not 28 <= len(payload) <= MAX_BUNDLE_BYTES:
        raise ValueError("加密包大小无效")
    try:
        return unpack(AESGCM(key).decrypt(payload[:12], payload[12:], AAD))
    except InvalidTag as error:
        raise ValueError("加密包认证失败") from error
