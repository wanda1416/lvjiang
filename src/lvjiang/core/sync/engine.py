"""文档通道同步引擎：本地文件 ↔ 云端 documents，整文档后写覆盖 + 冲突另存。

映射由 registry 静态声明，引擎只按登记枚举，不认识任何领域词汇：

- 用户级（``users/<用户名><后缀>``，云端 label = 用户名）：notes 与插件登记的
  ``.loadouts.json`` 一类；
- 账户级（会话目录内固定相对路径，云端 label = ``__global__``）：插件登记的
  跨角色共享文档。

用户资料与头像入服务端用户级数据（users_sync.py），profile.yaml 是定义走
definition 专用通道（definition_sync.py）——文档通道只承载用户内容。

胜负判定：同键 sha 不同时，云端 updated_at 与本地文件 mtime 比新；输的
一版另存 ``<文件>.conflict-<时间戳>``。服务端只存当前版。
"""
from __future__ import annotations

import base64
import gzip
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from ...constants import SESSION_CONFIG_DIR, USERS_DIR
from . import client, registry
from .client import SyncConflict, SyncError


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
                         session_dir: Path = SESSION_CONFIG_DIR) -> list[LocalDocument]:
    """枚举本机全部可同步文档：用户内容（notes 与插件 kind）+ 登记的账户级文档。

    用户级 kind 判定顺序：core 固有后缀（``.notes.json``）→ 排除项
    （``.session.json``）→ 插件经 registry 登记的后缀（最长匹配）→ 其余整文件
    都不入文档通道（资料已入表，定义走 definition）。core 只认自己拥有的语义，
    不认识任何插件领域词汇。
    """
    documents: list[LocalDocument] = []
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
        # 用户资料（{名}.json，无 kind 后缀）与头像已入服务端 account_users /
        # 其头像表；profile.yaml 是定义，走 definition 专用通道。这些是预期内
        # 的非文档文件，静默跳过；只有带点后缀却查不到 kind 的（如插件卸载后
        # 残留的 .loadouts.json）才是异常，告警提示。
        if "." not in name[: -len(".json")]:
            continue
        logger.warning("[sync] 跳过未登记 kind 的文档：{}", path.name)
    documents.extend(_session_documents(session_dir))
    return documents


def _session_documents(session_dir: Path) -> list[LocalDocument]:
    """登记的账户级文档中本地确实存在的那几份（label 固定 ``__global__``）。"""
    result: list[LocalDocument] = []
    for document in registry.session_documents().values():
        path = session_dir / document.rel_path
        if path.is_file():
            result.append(LocalDocument(registry.GLOBAL_LABEL, document.kind, path))
    return result


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


def _outbound_bytes(document: LocalDocument, raw: bytes) -> bytes:
    """上云前的边界变换：便利贴端到端加密（本地明文 → 云端密文）。

    无密钥时原样上云（读取端按 legacy 明文处理），不让加密层阻断同步。
    """
    if document.kind != "notes":
        return raw
    from ..account.e2e import current_key, encrypt_text
    key = current_key()
    if key is None:
        return raw
    return encrypt_text(key, raw.decode("utf-8")).encode("utf-8")


def _inbound_bytes(document: LocalDocument, payload: bytes) -> bytes | None:
    """下拉后的边界变换：便利贴解密。返回 None 表示保持本地文件不变。"""
    if document.kind != "notes":
        return payload
    from ..account.e2e import current_key, decrypt_text, is_encrypted_text
    text = payload.decode("utf-8", "replace")
    if not is_encrypted_text(text):
        return payload                  # legacy 明文：原样落地
    key = current_key()
    plain = decrypt_text(key, text) if key else None
    if plain is None:
        return None                     # 密文解不开：绝不用密文覆盖本地明文
    return plain.encode("utf-8")


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
    payload = _inbound_bytes(document, payload)
    if payload is None:
        # 密文但本机解不开（改密后旧数据等）：保留本地明文，不制造冲突副本
        report.pulled.append(key)
        return
    if keep_local and document.path.is_file():
        saved = _save_conflict(document, document.path.read_bytes())
        if saved is not None:
            report.conflicts.append(f"{key} → {saved.name}")
    _write_bytes(document, payload)
    report.pulled.append(key)
    # E2E 迁移钩子：拉到的是**明文**而本机有密钥 → 云端还没换密文。立即把
    # 密文推上去，否则会卡死在「明文 sha 相同 → 跳过推送」的循环里，明文
    # 永远留在云端（首次切换 E2E 时每个 notes 文档都会撞上）。
    if document.kind == "notes":
        from ..account.e2e import current_key, encrypt_text, is_encrypted_text
        e2e_key = current_key()
        if e2e_key is not None and not is_encrypted_text(payload.decode("utf-8", "replace")):
            encrypted = encrypt_text(e2e_key, payload.decode("utf-8")).encode("utf-8")
            _push(document, token, encrypted,
                  hashlib.sha256(encrypted).hexdigest(), None, report)


def _local_mtime(document: LocalDocument) -> datetime:
    return datetime.fromtimestamp(document.path.stat().st_mtime, tz=timezone.utc)


def sync_documents(token: str,
                   users_dir: Path = USERS_DIR,
                   session_dir: Path = SESSION_CONFIG_DIR,
                   on_progress=None, stop=None) -> SyncReport:
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
    local_docs = iter_local_documents(users_dir, session_dir)
    total_docs = len(local_docs)
    for index, entry in enumerate(local_docs):
        if stop and stop():
            report.errors.append("已停止")
            return report
        if on_progress:
            on_progress({"step": "documents", "detail": f"{index + 1}/{total_docs}"})
        key = (entry.label, entry.kind)
        seen.add(key)
        remote = remote_by_key.get(key)
        try:
            raw = _outbound_bytes(entry, _read_bytes(entry))
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
        document: LocalDocument | None = _document_for_remote(label, kind, users_dir, session_dir)
        if document is None:
            continue
        try:
            _pull(document, token, report)
        except SyncError as error:
            report.errors.append(f"{label}/{kind}: {error}")
    # 不在这里打完成日志：唯一调用方（PC 账户对话框）会用 _CombinedReport
    # 打一行覆盖四通道的汇总，内层再打一份纯重复；失败已逐条进 errors。
    return report


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _document_for_remote(label: str, kind: str,
                         users_dir: Path,
                         session_dir: Path) -> LocalDocument | None:
    """远端独有文档的本地落点；反推不出物理路径（如空 label）时跳过。"""
    if kind == "notes":
        return LocalDocument(label, kind, users_dir / f"{label}.notes.json")
    # 账户级文档：落点由登记决定，与云端 label 无关（必须是 __global__）。
    session_document = registry.session_document_for(kind)
    if session_document is not None:
        if label != registry.GLOBAL_LABEL:
            return None
        return LocalDocument(label, kind, session_dir / session_document.rel_path)
    # 插件登记的用户级 kind：按登记的后缀落盘；未登记（本机没加载该插件）→
    # 不落，云端该文档保持原样，等装了插件的设备再拉取。
    suffix = registry.user_document_kinds().get(kind)
    if suffix is not None:
        return LocalDocument(label, kind, users_dir / f"{label}{suffix}")
    return None
