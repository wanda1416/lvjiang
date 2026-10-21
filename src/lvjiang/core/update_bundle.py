"""0.15 配置包协议；构建 APK 时无需导入配置运行时。"""
from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import zipfile
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

from .versioning import application_series

SCHEMA_VERSION = 1
MANIFEST_SCHEMA_VERSION = 2
PUBLIC_INDEX_URL = "https://wanda1416.github.io/lvjiang/public/index.json"
MEMBER_INDEX_URL = "https://wanda1416.github.io/lvjiang/member/index.json"
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_FILES = 10000


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def series(version: str) -> str:
    """应用映射到目标三段系列；清单 key 仍必须是三段正式系列。"""
    return application_series(version)


def next_series(version: str) -> str:
    major, minor, patch = map(int, series(version).split("."))
    return f"{major}.{minor}.{patch + 1}"


def safe_path(name: str) -> bool:
    """跨平台相对文件路径；不以扩展名或客户端类型注册表作为下发白名单。"""
    if not isinstance(name, str) or not name or name.startswith("/") or "\\" in name:
        return False
    from .license_files import is_private_license_file
    if is_private_license_file(name):
        return False
    return all(part not in {"", ".", ".."} and not part.endswith((".", " "))
               and not re.search(r'[<>:"|?*\x00-\x1f]', part)
               and not re.fullmatch(r"(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])", part.split(".")[0])
               for part in name.split("/"))


def validate_names(names: Any) -> None:
    seen: set[str] = set()
    for name in names:
        if not safe_path(name) or name.casefold() in seen:
            raise ValueError(f"不安全或大小写冲突的配置路径: {name!r}")
        seen.add(name.casefold())
    for name in seen:
        parts = name.split("/")
        if any("/".join(parts[:index]) in seen for index in range(1, len(parts))):
            raise ValueError(f"配置文件与目录冲突: {name}")
    if len(seen) > MAX_FILES:
        raise ValueError("配置文件数量超限")


def read_tree(root: Path, *, only: Collection[str] | None = None) -> dict[str, bytes]:
    """读取明确的官方源或隔离候选目录，不跳过未知文件。

    ``only`` 给定时只读取其中的相对路径，其余条目连名字校验和体积统计都不做：
    打包官方预置时用它排除工作区里未纳入 git 的草稿，名字或体积不合规的草稿
    就不会在「被排除之前」先把构建卡住。
    """
    if not root.is_dir() or root.is_symlink():
        raise ValueError("配置源必须是普通目录")
    wanted = set(only) if only is not None else None
    files: dict[str, bytes] = {}
    total = 0
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        if wanted is not None and name not in wanted:
            continue
        if path.is_symlink():
            raise ValueError("配置源不允许符号链接")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("配置源不允许特殊文件")
        size = path.stat().st_size
        total += size
        if size > MAX_FILE_BYTES or total > MAX_EXPANDED_BYTES:
            raise ValueError("配置源大小超限")
        files[name] = path.read_bytes()
    validate_names(files)
    return files


def file_hashes(files: Mapping[str, bytes]) -> dict[str, str]:
    validate_names(files)
    return {name: digest(data) for name, data in sorted(files.items())}


def tree_digest(files: Mapping[str, bytes]) -> str:
    return digest(json_bytes(file_hashes(files)))


def pack(files: Mapping[str, bytes]) -> bytes:
    """确定性 ZIP；允许空累计增量，代表撤回全部在线覆盖。"""
    validate_names(files)
    if any(len(data) > MAX_FILE_BYTES for data in files.values()) \
            or sum(map(len, files.values())) > MAX_EXPANDED_BYTES:
        raise ValueError("配置包展开大小超限")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, data)
    result = stream.getvalue()
    if len(result) > MAX_ARCHIVE_BYTES:
        raise ValueError("配置压缩包大小超限")
    return result


def unpack(payload: bytes) -> dict[str, bytes]:
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise ValueError("配置压缩包大小超限")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = archive.infolist()
            validate_names(entry.filename for entry in entries)
            total = 0
            result = {}
            for entry in entries:
                kind = stat.S_IFMT(entry.external_attr >> 16)
                if entry.is_dir() or kind not in (0, stat.S_IFREG) or entry.flag_bits & 1:
                    raise ValueError("配置包仅允许普通文件")
                total += entry.file_size
                if entry.file_size > MAX_FILE_BYTES or total > MAX_EXPANDED_BYTES:
                    raise ValueError("配置包展开大小超限")
                result[entry.filename] = archive.read(entry)
            return result
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
        raise ValueError("无效配置压缩包") from error


def _hash(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))


def parse_manifest(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError("不支持的公开配置清单协议")
    version = data.get("app_series")
    if not isinstance(version, str) or series(version) != version:
        raise ValueError("清单必须声明三段应用系列")
    revision = data.get("revision")
    if type(revision) is not int or revision < 1:
        raise ValueError("公开修订必须是正整数")
    if not _hash(data.get("baseline_sha256")) or not _hash(data.get("archive_sha256")):
        raise ValueError("配置包或预置基底摘要无效")
    files = data.get("files")
    if not isinstance(files, dict) or not all(_hash(value) for value in files.values()):
        raise ValueError("配置文件摘要表无效")
    validate_names(files)
    deleted = data.get("delete_files")
    if not isinstance(deleted, list) or not all(isinstance(name, str) for name in deleted) \
            or len(deleted) != len(set(deleted)):
        raise ValueError("配置删除文件清单无效")
    validate_names(deleted)
    validate_names([*files, *deleted])
    ancestry = data.get("ancestors", {})
    if not isinstance(ancestry, dict) or any(
            not isinstance(key, str) or not re.fullmatch(r"[1-9]\d*", key) or int(key) >= revision or not _hash(value)
            for key, value in ancestry.items()):
        raise ValueError("公开修订来源摘要无效")
    return data


def make_manifest(version: str, revision: int, baseline: Mapping[str, bytes],
                  files: Mapping[str, bytes], *, delete_files: tuple[str, ...] = (),
                  ancestors: Mapping[str, str] | None = None) -> tuple[dict[str, Any], bytes]:
    archive = pack(files)
    manifest = parse_manifest({
        "schema_version": MANIFEST_SCHEMA_VERSION, "app_series": version, "revision": revision,
        "baseline_sha256": tree_digest(baseline), "archive_sha256": digest(archive),
        "files": file_hashes(files), "delete_files": sorted(delete_files),
        "ancestors": dict(ancestors or {}),
    })
    return manifest, archive


def verify_archive(manifest: Mapping[str, Any], archive: bytes) -> dict[str, bytes]:
    if digest(archive) != manifest["archive_sha256"]:
        raise ValueError("配置包摘要不匹配")
    files = unpack(archive)
    if file_hashes(files) != manifest["files"]:
        raise ValueError("配置包文件清单与内容不一致")
    return files


def parse_index(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION \
            or not isinstance(data.get("series"), dict):
        raise ValueError("不支持的公开配置索引协议")
    for version, entry in data["series"].items():
        if series(version) != version or not isinstance(entry, dict):
            raise ValueError("索引应用系列无效")
        revision = entry.get("revision")
        if type(revision) is not int or revision < 1 \
                or entry.get("manifest") != f"v{version}/r{revision}/manifest.json" \
                or not _hash(entry.get("sha256")):
            raise ValueError("索引修订或清单地址无效")
    return data
