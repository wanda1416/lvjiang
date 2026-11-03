"""文档通道同步引擎：本地文件 ↔ 云端 documents，整文档后写覆盖 + 冲突另存。

映射：users/{名}.json → user_doc（push 时剥离 ui_state，pull 时保留本地
ui_state——界面偏好永远以本机为准）；{名}.loadouts.json → loadouts；
{名}.notes.json（便利贴）→ notes；profile.yaml → profile_yaml（账户级
label __global__）；用户头像引用的 avatars/ 文件 → avatar。

胜负判定：同键 sha 不同时，云端 updated_at 与本地文件 mtime 比新；输的
一版另存 ``<文件>.conflict-<时间戳>``。服务端只存当前版。
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from ...constants import AVATARS_DIR, SESSION_CONFIG_DIR, USERS_DIR
from ..json_files import formatted_json
from . import client
from .client import SyncConflict, SyncError

GLOBAL_LABEL = "__global__"


@dataclass
class LocalDocument:
    """一个可同步的本地文档：物理路径 + 云端 (label, kind)。"""

    label: str
    kind: str
    path: Path
    #: JSON 文档 push 剥离 ui_state / pull 保留本地 ui_state
    strip_ui_state: bool = False


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


def iter_local_documents(users_dir: Path = USERS_DIR,
                         avatars_dir: Path = AVATARS_DIR,
                         session_dir: Path = SESSION_CONFIG_DIR) -> list[LocalDocument]:
    """枚举本机全部可同步文档；头像只取用户文档实际引用的文件。"""
    documents: list[LocalDocument] = []
    avatars: set[str] = set()
    for path in sorted(users_dir.glob("*.json")):
        name = path.name
        # 会话运行态（users/{名}.session.json）不属于同步范围，永不入云。
        if name.endswith(".session.json"):
            continue
        if name.endswith(".loadouts.json"):
            documents.append(LocalDocument(name[:-len(".loadouts.json")], "loadouts", path))
        elif name.endswith(".notes.json"):
            documents.append(LocalDocument(name[:-len(".notes.json")], "notes", path))
        else:
            documents.append(LocalDocument(path.stem, "user_doc", path, strip_ui_state=True))
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
    if document.strip_ui_state:
        data = json.loads(document.path.read_text(encoding="utf-8"))
        data.pop("ui_state", None)
        return formatted_json(data).encode("utf-8")
    return document.path.read_bytes()


def _write_bytes(document: LocalDocument, payload: bytes) -> None:
    document.path.parent.mkdir(parents=True, exist_ok=True)
    if document.strip_ui_state:
        data = json.loads(payload.decode("utf-8"))
        # 本机 ui_state 永远以本地为准：远端版本不带它，带上本机现值再落盘。
        if document.path.is_file():
            try:
                local_ui = json.loads(
                    document.path.read_text(encoding="utf-8")).get("ui_state")
            except (OSError, ValueError):
                local_ui = None
            if isinstance(local_ui, dict):
                data["ui_state"] = local_ui
        document.path.write_text(formatted_json(data), encoding="utf-8")
    else:
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


def _pull(document: LocalDocument, token: str, report: SyncReport) -> None:
    key = f"{document.label}/{document.kind}"
    remote = client.pull(token, document.label, document.kind)
    payload = base64.b64decode(remote["payload_base64"])
    if remote.get("zipped"):
        payload = gzip.decompress(payload)
    if document.path.is_file():
        saved = _save_conflict(document, document.path.read_bytes())
        if saved is not None:
            report.conflicts.append(f"{key} → {saved.name}")
    _write_bytes(document, payload)
    report.pulled.append(key)


def _local_mtime(document: LocalDocument) -> datetime:
    return datetime.fromtimestamp(document.path.stat().st_mtime, tz=UTC)


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
                    # 本机 mtime 较新但云端又先写入了（竞态）：按冲突规则让云端赢。
                    _pull(entry, token, report)
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
    if kind in ("user_doc", "loadouts", "notes"):
        suffix = {"user_doc": ".json", "loadouts": ".loadouts.json", "notes": ".notes.json"}[kind]
        return LocalDocument(label, kind, users_dir / f"{label}{suffix}",
                             strip_ui_state=kind == "user_doc")
    return None
