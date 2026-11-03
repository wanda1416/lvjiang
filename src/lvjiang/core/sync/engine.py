"""文档通道同步引擎：本地文件 ↔ 云端 documents，整文档后写覆盖 + 冲突另存。

映射：{名}.loadouts.json → loadouts（插件 kind，经 registry 登记）；
{名}.notes.json（便利贴）→ notes；profile.yaml → profile_yaml（账户级
label __global__）；用户头像引用的 avatars/ 文件 → avatar。
用户资料（users/{名}.json）**不走文档通道**——入服务端 account_users 表
（见 users_sync.py），这里只读它引用的头像文件名。

胜负判定：同键 sha 不同时，云端 updated_at 与本地文件 mtime 比新；输的
一版另存 ``<文件>.conflict-<时间戳>``。服务端只存当前版。
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from ...constants import AVATARS_DIR, SESSION_CONFIG_DIR, USERS_DIR
from ..user_config import is_valid_username
from . import client, registry
from .client import SyncConflict, SyncError

GLOBAL_LABEL = "__global__"


@dataclass
class LocalDocument:
    """一个可同步的本地文档：物理路径 + 云端 (label, kind)。"""

    label: str
    kind: str
    path: Path


@dataclass
class SyncReport:
    pushed: list[str] = field(default_factory=list)
    pulled: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def describe(self) -> str:
        if not (self.pushed or self.pulled or self.conflicts or self.errors):
            return "已是最新，无改动"
        parts = [f"上行 {len(self.pushed)}、下行 {len(self.pulled)}"]
        if self.conflicts:
            parts.append(f"冲突另存 {len(self.conflicts)}")
        if self.errors:
            parts.append(f"失败 {len(self.errors)}")
        return "，".join(parts)


#: core 自己拥有的用户级文档后缀（优先于插件登记，插件不得抢占）。
_CORE_SUFFIXES: tuple[tuple[str, str], ...] = (("notes", ".notes.json"),)
#: 永不参与同步的会话运行态后缀。
_EXCLUDED_SUFFIXES: tuple[str, ...] = (".session.json",)


def iter_local_documents(users_dir: Path = USERS_DIR,
                         avatars_dir: Path = AVATARS_DIR,
                         session_dir: Path = SESSION_CONFIG_DIR) -> list[LocalDocument]:
    """枚举本机全部可同步文档；头像只取用户文档实际引用的文件。

    kind 判定顺序：core 固有后缀（``.notes.json``）→ 排除项（``.session.json``）
    → 插件经 registry 登记的后缀（最长匹配）→ 其余整文件即 ``user_doc``。
    core 只认自己拥有的语义，不认识任何插件领域词汇。
    """
    documents: list[LocalDocument] = []
    avatars: set[str] = set()
    plugin_suffixes: list[tuple[str, str]] = sorted(
        registry.user_document_kinds().items(), key=lambda item: -len(item[1]))
    for path in sorted(users_dir.glob("*.json")):
        name = path.name
        if any(name.endswith(suffix) for suffix in _EXCLUDED_SUFFIXES):
            continue
        core = next(((kind, suffix) for kind, suffix in _CORE_SUFFIXES
                     if name.endswith(suffix)), None)
        matched = core or next(
            ((kind, suffix) for kind, suffix in plugin_suffixes if name.endswith(suffix)), None)
        if matched is not None:
            kind, suffix = matched
            documents.append(LocalDocument(name[:-len(suffix)], kind, path))
            continue
        # 用户资料（{名}.json）已入服务端 account_users 表，不再作为文档上云；
        # 这里只读它引用的头像文件名（头像二进制仍是文档）。
        if not is_valid_username(path.stem):
            logger.warning("[sync] 跳过未登记 kind 的文档：{}", path.name)
            continue
        try:
            avatar = json.loads(path.read_text(encoding="utf-8")).get("avatar")
        except (OSError, ValueError):
            continue
        if isinstance(avatar, str) and avatar:
            avatars.add(avatar)
    for filename in sorted(avatars):
        path = avatars_dir / filename
        if path.is_file():
            documents.append(LocalDocument(filename, "avatar", path))
    profile_yaml = session_dir / "profile.yaml"
    if profile_yaml.is_file():
        documents.append(LocalDocument(GLOBAL_LABEL, "profile_yaml", profile_yaml))
    return documents


def _read_bytes(document: LocalDocument) -> bytes:
    return document.path.read_bytes()


def _write_bytes(document: LocalDocument, payload: bytes) -> None:
    document.path.parent.mkdir(parents=True, exist_ok=True)
    document.path.write_bytes(payload)


def _save_conflict(document: LocalDocument, local_raw: bytes) -> Path | None:
    """本地将被远端覆盖前，把输的本地版本另存（可审计、可手工恢复）。"""
    if not document.path.is_file():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    conflict = document.path.with_name(f"{document.path.name}.conflict-{stamp}")
    conflict.write_bytes(local_raw)
    return conflict


def _push(document: LocalDocument, token: str, raw: bytes, sha256: str,
          expected: str | None, report: SyncReport) -> None:
    key = f"{document.label}/{document.kind}"
    encoded = base64.b64encode(raw).decode("ascii")
    if document.kind == "avatar":
        payload = encoded                      # 图片已是压缩格式，原样传输
    else:
        payload = base64.b64encode(gzip.compress(raw)).decode("ascii")
    client.push(token, document.label, document.kind, sha256, payload,
                expected_sha256=expected)
    report.pushed.append(key)


def _pull(document: LocalDocument, token: str, report: SyncReport,
          *, keep_local: bool = False) -> None:
    """把云端版本落到本地。``keep_local`` 为真时先把本地版另存为冲突副本。

    只有**本地确实有更新的独立改动、却被云端覆盖**时才算冲突。本地只是旧版
    时直接覆盖即可——给每个旧版都留一份副本只会堆垃圾（实测会导致所有
    notes.json 每次同步都多出一个 .conflict 文件）。
    """
    key = f"{document.label}/{document.kind}"
    remote = client.pull(token, document.label, document.kind)
    payload = base64.b64decode(remote["payload_base64"])
    if remote.get("zipped"):
        payload = gzip.decompress(payload)
    if keep_local and document.path.is_file():
        saved = _save_conflict(document, document.path.read_bytes())
        if saved is not None:
            report.conflicts.append(f"{key} → {saved.name}")
    _write_bytes(document, payload)
    report.pulled.append(key)


def _local_mtime(document: LocalDocument) -> datetime:
    return datetime.fromtimestamp(document.path.stat().st_mtime, tz=timezone.utc)


def sync_documents(token: str,
                   users_dir: Path = USERS_DIR,
                   avatars_dir: Path = AVATARS_DIR,
                   session_dir: Path = SESSION_CONFIG_DIR) -> SyncReport:
    """全量对账一次：本地枚举 + 远端 manifest，按键做后写覆盖；失败逐条记账不中断。"""
    report = SyncReport()
    remote_by_key: dict[tuple[str, str], dict] = {}
    try:
        for remote_entry in client.manifest(token):
            remote_by_key[(remote_entry["label"], remote_entry["kind"])] = remote_entry
    except SyncError as error:
        report.errors.append(str(error))
        return report
    seen: set[tuple[str, str]] = set()
    for entry in iter_local_documents(users_dir, avatars_dir, session_dir):
        key = (entry.label, entry.kind)
        seen.add(key)
        remote = remote_by_key.get(key)
        try:
            raw = _read_bytes(entry)
            sha256 = hashlib.sha256(raw).hexdigest()
            if remote is None:
                _push(entry, token, raw, sha256, None, report)
            elif remote["sha256"] == sha256:
                continue
            elif _local_mtime(entry) >= _parse_utc(remote["updated_at"]):
                try:
                    _push(entry, token, raw, sha256, remote["sha256"], report)
                except SyncConflict:
                    # 本机 mtime 较新但云端又先写入了（竞态）：云端赢，但本地这版
                    # 是真实的独立改动，必须另存，否则改动静默丢失。
                    _pull(entry, token, report, keep_local=True)
            else:
                _pull(entry, token, report)
        except (OSError, ValueError) as error:
            report.errors.append(f"{key}: 本地读取失败 {error}")
        except SyncError as error:
            report.errors.append(f"{key}: {error}")
    # 远端有、本地没有的文档（另一台设备新建的用户/头像/全局定义）→ 下行
    for (label, kind) in remote_by_key:
        if (label, kind) in seen:
            continue
        document: LocalDocument | None = _document_for_remote(label, kind, users_dir, avatars_dir, session_dir)
        if document is None:
            continue
        try:
            _pull(document, token, report)
        except SyncError as error:
            report.errors.append(f"{label}/{kind}: {error}")
    logger.info("[sync] {}", report.describe())
    return report


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _document_for_remote(label: str, kind: str,
                         users_dir: Path, avatars_dir: Path,
                         session_dir: Path) -> LocalDocument | None:
    """远端独有文档的本地落点；反推不出物理路径（如空 label）时跳过。"""
    if kind == "profile_yaml":
        return LocalDocument(label, kind, session_dir / "profile.yaml")
    if kind == "avatar":
        return LocalDocument(label, kind, avatars_dir / label)
    if kind == "notes":
        return LocalDocument(label, kind, users_dir / f"{label}.notes.json")
    # 插件登记的 kind：按登记的后缀落盘；未登记（本机没加载该插件）→ 不落，
    # 云端该文档保持原样，等装了插件的设备再拉取。
    suffix = registry.user_document_kinds().get(kind)
    if suffix is not None:
        return LocalDocument(label, kind, users_dir / f"{label}{suffix}")
    return None
