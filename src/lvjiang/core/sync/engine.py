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
from ..fs_util import atomic_write_bytes, locked_paths
from . import client, dirty, registry
from .client import SyncConflict, SyncError, post
from .round_state import cloud_state, invalidate, version


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


def _write_bytes(document: LocalDocument, payload: bytes, *, expected: bytes | None) -> None:
    if document.kind == "workflow":
        from ..config.workflow_document import validate_workflow_document
        validate_workflow_document(payload, document.path)
    with locked_paths(document.path):
        current = document.path.read_bytes() if document.path.exists() else None
        if current != expected:
            raise OSError(f"{document.path.name} 下行期间已被编辑，保留本地修改并稍后重试")
        atomic_write_bytes(document.path, payload, prefix=".document-")


def _conflict_copy(document: LocalDocument) -> bool:
    """冲突输方是否另存副本；登记 ``conflict_copy=False`` 的会话文档不留
    （数据可丢的文档，如任务参数/批量配置：云端最新版直接覆盖）。"""
    session_document = registry.session_document_for(document.kind)
    return True if session_document is None else session_document.conflict_copy


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
    invalidate()                          # 写过云端：后续步骤重新取版本行
    report.pushed.append(key)


def _outbound_bytes(document: LocalDocument, raw: bytes) -> bytes:
    """上云前的边界变换：全部文档端到端加密（本地明文 → 先压缩再加密）。

    无密钥时原样上云（读取端按 legacy 明文处理），不让加密层阻断同步。
    """
    from ..account.e2e import current_key, encrypt_text
    key = current_key()
    if key is None:
        return raw
    return encrypt_text(key, raw.decode("utf-8")).encode("utf-8")


def _inbound_bytes(document: LocalDocument, payload: bytes) -> bytes | None:
    """下拉后的边界变换：全文档解密。返回 None 表示保持本地文件不变。"""
    from ..account.e2e import current_key, decrypt_text, is_encrypted_text
    text = payload.decode("utf-8", "replace")
    if not is_encrypted_text(text):
        return payload                  # legacy 明文：原样落地
    key = current_key()
    plain = decrypt_text(key, text) if key else None
    if plain is None:
        return None                     # 密文解不开：绝不用密文覆盖本地明文
    return plain.encode("utf-8")


def _is_plain_legacy(raw: bytes) -> bool:
    """远端文档字节是否仍是 legacy 明文（未套 E2E 封装）。"""
    from ..account.e2e import is_encrypted_text
    return not is_encrypted_text(raw.decode("utf-8", "replace"))


def _pull(document: LocalDocument, token: str, report: SyncReport,
          *, keep_local: bool = False, raw: bytes | None = None) -> None:
    """把云端版本落到本地。``keep_local`` 为真时先把本地版另存为冲突副本。"""
    key = f"{document.label}/{document.kind}"
    before = document.path.read_bytes() if document.path.exists() else None
    if raw is None:
        remote = client.pull(token, document.label, document.kind)
        raw = base64.b64decode(remote["payload_base64"])
        if remote.get("zipped"):
            raw = gzip.decompress(raw)
    remote_was_plaintext = _is_plain_legacy(raw)
    payload = _inbound_bytes(document, raw)
    if payload is None:
        # 密文但本机解不开（改密后旧数据/未输口令）：保留本地明文，登记待解密集合
        pend = dirty.pending_decrypt()
        if key not in pend:
            pend.add(key)
            dirty.set_pending_decrypt(pend)
        report.pulled.append(key)
        return
    pend_dec = dirty.pending_decrypt()
    if key in pend_dec:
        pend_dec.discard(key)
        dirty.set_pending_decrypt(pend_dec)
    if keep_local and _conflict_copy(document) and document.path.is_file():
        saved = _save_conflict(document, document.path.read_bytes())
        if saved is not None:
            report.conflicts.append(f"{key} → {saved.name}")
    _write_bytes(document, payload, expected=before)
    report.pulled.append(key)
    # E2E 迁移钩子：拉到的是明文而本机有密钥 → 立即以 CAS 重推密文。
    # 若遇并发 409 冲突则静默重拉；若遇离线/失败则登记 pending_encrypt 下次补推。
    from ..account.e2e import current_key, encrypt_text
    e2e_key = current_key()
    if e2e_key is not None and remote_was_plaintext:
        encrypted = encrypt_text(e2e_key, payload.decode("utf-8")).encode("utf-8")
        plain_sha = hashlib.sha256(raw).hexdigest()
        enc_sha = hashlib.sha256(encrypted).hexdigest()
        try:
            _push(document, token, encrypted, enc_sha, plain_sha, report)
            pend_enc = dirty.pending_encrypt()
            if key in pend_enc:
                pend_enc.discard(key)
                dirty.set_pending_encrypt(pend_enc)
        except SyncConflict:
            # 另一台设备同时完成了静默加密升级，静默重拉确认即可
            try:
                latest = client.pull(token, document.label, document.kind)
                latest_raw = base64.b64decode(latest["payload_base64"])
                if latest.get("zipped"):
                    latest_raw = gzip.decompress(latest_raw)
                if not _is_plain_legacy(latest_raw):
                    latest_plain = _inbound_bytes(document, latest_raw)
                    if latest_plain is not None:
                        _write_bytes(document, latest_plain, expected=payload)
            except SyncError:
                pass
        except SyncError as error:
            pend_enc = dirty.pending_encrypt()
            pend_enc.add(key)
            dirty.set_pending_encrypt(pend_enc)
            logger.debug("[sync] 文档 {} 密文化重推延后：{}", key, error)


def _local_mtime(document: LocalDocument) -> datetime:
    return datetime.fromtimestamp(document.path.stat().st_mtime, tz=timezone.utc)


def _delta_page(token: str, since: int) -> tuple[list[dict], int]:
    page = post("/api/sync/documents/pull", {"since_revision": since}, token)
    return list(page.get("documents") or []), int(page.get("next_id") or 0)


def _local_stamps(local_docs: list[LocalDocument]) -> dict[str, list[int]]:
    """每份本地文档的 ``[size, mtime_ns]``（只 stat，不读内容）。"""
    result: dict[str, list[int]] = {}
    for document in local_docs:
        try:
            stat = document.path.stat()
        except OSError:
            continue
        result[f"{document.label}/{document.kind}"] = [stat.st_size, stat.st_mtime_ns]
    return result


def _detect_local_changes(local_docs: list[LocalDocument]) -> set[str]:
    """磁盘戳与上次同步不一致的文档键。

    这是「谁改的都能发现」的那一半：只读实例、外部编辑器、备份还原改过的
    文件不会调用 ``mark_dirty``，只能靠 stat 比对认出来。
    """
    cached = dirty.stamps()
    changed: set[str] = set()
    for document in local_docs:
        key = f"{document.label}/{document.kind}"
        try:
            stat = document.path.stat()
        except OSError:
            continue
        known = cached.get(key)
        if known is None or known[0] != stat.st_size or known[1] != stat.st_mtime_ns:
            changed.add(key)
    return changed


def _reconcile_e2e_migration(token: str, users_dir: Path, session_dir: Path,
                             report: SyncReport) -> None:
    """一次性密文化核对与待迁移补推：仅在密钥指纹变化或有待迁移项时执行。"""
    from ..account.e2e import current_key, encrypt_text, key_fingerprint

    key = current_key()
    if key is None:
        return
    fp = key_fingerprint(key)

    # 1. 重试此前解密失败的文档（恢复/更换正确密钥后自动重拉）
    for item_key in sorted(dirty.pending_decrypt()):
        label, _, kind = item_key.partition("/")
        doc = _document_for_remote(label, kind, users_dir, session_dir)
        if doc is None:
            continue
        try:
            _pull(doc, token, report)
        except SyncError as error:
            report.errors.append(f"{item_key}: {error}")

    # 2. 比对 manifest：远端仍是明文 sha256 的文档静默加密重推
    local_docs = iter_local_documents(users_dir, session_dir)
    by_key = {(d.label, d.kind): d for d in local_docs}
    pend_enc = dirty.pending_encrypt()
    try:
        manifest_rows = client.manifest(token)
    except SyncError as error:
        report.errors.append(str(error))
        return

    unmanaged_count = 0
    for row in manifest_rows:
        r_label, r_kind = row.get("label", ""), row.get("kind", "")
        doc = by_key.get((r_label, r_kind))
        if doc is None:
            unmanaged_count += 1
            continue
        if not doc.path.is_file():
            continue
        raw = _read_bytes(doc)
        plain_sha = hashlib.sha256(raw).hexdigest()
        doc_key = f"{doc.label}/{doc.kind}"
        if row.get("sha256") == plain_sha or doc_key in pend_enc:
            encrypted = encrypt_text(key, raw.decode("utf-8")).encode("utf-8")
            enc_sha = hashlib.sha256(encrypted).hexdigest()
            try:
                _push(doc, token, encrypted, enc_sha, row.get("sha256"), report)
                pend_enc.discard(doc_key)
                dirty.set_pending_encrypt(pend_enc)
            except SyncConflict:
                # 并发device刚升完密文，静默重拉确认即可
                try:
                    _pull(doc, token, report)
                except SyncError as error:
                    report.errors.append(f"{doc_key}: {error}")
            except SyncError as error:
                pend_enc.add(doc_key)
                dirty.set_pending_encrypt(pend_enc)
                report.errors.append(f"{doc_key}: {error}")

    if unmanaged_count:
        logger.info("[sync] 云端存在 {} 份本机未管理插件的文档（跳过本机迁移校验）", unmanaged_count)

    if not report.errors and not dirty.pending_decrypt() and not dirty.pending_encrypt():
        dirty.set_e2e_migrated_key_fp(fp)


def sync_documents(token: str, users_dir: Path = USERS_DIR,
                   session_dir: Path = SESSION_CONFIG_DIR,
                   on_progress=None, stop=None,
                   force_full: bool = False) -> SyncReport:
    """增量对账：远端 revision 水位 + 本地改动（标脏 ∪ 磁盘戳）。

    四种情况：都没变 → 空转；只远端变 → 拉增量；只本地变 → 推脏；都变 →
    全量 manifest + mtime 对比裁决。
    """
    report = SyncReport()
    local_docs = iter_local_documents(users_dir, session_dir)
    # 首次运行（没有任何戳）时无法判断「谁改的」，交给 mtime 全量对比定胜负，
    # 之后戳就建起来了。否则会把本地全部文档当成「刚改过」直接推上云。
    first_run = not dirty.stamps()
    local_dirty = dirty.dirty_keys() | _detect_local_changes(local_docs)

    if force_full or first_run:
        # 初始同步 / 首次运行：本地已有全量数据要上云或需要裁决，直接全量。
        _sync_documents_full(token, users_dir, session_dir, on_progress, stop, report)
        try:
            _changed, next_id = _delta_page(token, dirty.remote_document_id())
        except SyncError as error:
            report.errors.append(str(error))
            return report
        dirty.save_sync_state(max(next_id - 1, 0), set(),
                              _local_stamps(iter_local_documents(users_dir, session_dir)))
        return report

    remote_id = dirty.remote_document_id()
    # 同步版本行的文档 head 与本地水位相同 = 云端没有新文档：不发增量拉取。
    # head 取不到（网络/老服务端）就照常拉增量页判断。
    try:
        cloud_head = version(cloud_state(token, post), "document_head")
    except SyncError:
        cloud_head = None
    changed: list[dict]
    if isinstance(cloud_head, int) and cloud_head == remote_id:
        changed, next_id = [], remote_id + 1
    else:
        try:
            changed, next_id = _delta_page(token, remote_id)
        except SyncError as error:
            report.errors.append(str(error))
            return report
    remote_changed = bool(changed)
    local_changed = bool(local_dirty)

    from ..account.e2e import current_key, key_fingerprint
    e2e_key = current_key()
    needs_e2e_check = (
        e2e_key is not None
        and (
            dirty.e2e_migrated_key_fp() != key_fingerprint(e2e_key)
            or bool(dirty.pending_decrypt())
            or bool(dirty.pending_encrypt())
        )
    )

    if not remote_changed and not local_changed:
        if needs_e2e_check:
            _reconcile_e2e_migration(token, users_dir, session_dir, report)
        return report

    if remote_changed and not local_changed:
        # 只拉变化文档
        for entry in changed:
            if stop and stop():
                report.errors.append("已停止")
                break
            document = _document_for_remote(entry["label"], entry["kind"],
                                            users_dir, session_dir)
            if document is None:
                continue
            try:
                raw = base64.b64decode(entry["payload_base64"])
                if entry.get("zipped"):
                    raw = gzip.decompress(raw)
                _pull(document, token, report, raw=raw)
            except (ValueError, OSError) as error:
                report.errors.append(f"{entry['label']}/{entry['kind']}: {error}")
            except SyncError as error:
                report.errors.append(f"{entry['label']}/{entry['kind']}: {error}")
        if not report.errors:
            dirty.save_sync_state(next_id - 1, set(), _local_stamps(local_docs))
            if needs_e2e_check:
                _reconcile_e2e_migration(token, users_dir, session_dir, report)
        return report

    if local_changed and not remote_changed:
        # 只推脏集合
        for key in sorted(local_dirty):
            if stop and stop():
                report.errors.append("已停止")
                break
            label, _, kind = key.partition("/")
            document = _document_for_remote(label, kind, users_dir, session_dir)
            if document is None or not document.path.is_file():
                continue
            try:
                raw = _outbound_bytes(document, _read_bytes(document))
                sha256 = hashlib.sha256(raw).hexdigest()
                _push(document, token, raw, sha256, None, report)
            except (OSError, ValueError) as error:
                report.errors.append(f"{key}: {error}")
            except SyncError as error:
                report.errors.append(f"{key}: {error}")
        if not report.errors:
            dirty.save_sync_state(next_id - 1, set(), _local_stamps(local_docs))
            if needs_e2e_check:
                _reconcile_e2e_migration(token, users_dir, session_dir, report)
        return report

    # 双变：全量 manifest + 本地 mtime 对比裁决，之后清脏、抬水位。
    _sync_documents_full(token, users_dir, session_dir, on_progress, stop, report)
    if not report.errors:
        dirty.save_sync_state(next_id - 1, set(),
                              _local_stamps(iter_local_documents(users_dir, session_dir)))
        if needs_e2e_check:
            _reconcile_e2e_migration(token, users_dir, session_dir, report)
    return report


def _sync_documents_full(token: str, users_dir: Path, session_dir: Path,
                         on_progress, stop, report: SyncReport) -> None:
    """全量对账（双变冲突时的裁决路径）：本地枚举 + 远端 manifest，mtime 最新者胜。"""
    remote_by_key: dict[tuple[str, str], dict] = {}
    try:
        for remote_entry in client.manifest(token):
            remote_by_key[(remote_entry["label"], remote_entry["kind"])] = remote_entry
    except SyncError as error:
        report.errors.append(str(error))
        return
    seen: set[tuple[str, str]] = set()
    local_docs = iter_local_documents(users_dir, session_dir)
    total_docs = len(local_docs)
    logger.info("[sync] 文档全量对账（双变冲突）：本地 {} 份，远端 {} 份",
                total_docs, len(remote_by_key))
    # 批量拉取全部远端 payload（替代逐份 pull）。
    remote_raw: dict[tuple[str, str], bytes] = {}
    try:
        for remote_entry in client.pull_all(token):
            key = (remote_entry["label"], remote_entry["kind"])
            try:
                raw = base64.b64decode(remote_entry["payload_base64"])
                if remote_entry.get("zipped"):
                    raw = gzip.decompress(raw)
                remote_raw[key] = raw
            except (ValueError, OSError):
                continue      # 坏条目不入批量表，走单份接口兜底
    except SyncError as error:
        report.errors.append(str(error))
    for index, entry in enumerate(local_docs):
        if stop and stop():
            report.errors.append("已停止")
            return
        if on_progress:
            on_progress({"step": "documents", "detail": f"{index + 1}/{total_docs}"})
        key = (entry.label, entry.kind)
        seen.add(key)
        remote = remote_by_key.get(key)
        batch_raw = remote_raw.get(key)
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
                    _pull(entry, token, report, keep_local=True, raw=batch_raw)
            else:
                _pull(entry, token, report, raw=batch_raw)
        except (OSError, ValueError) as error:
            report.errors.append(f"{key}: 本地读取失败 {error}")
        except SyncError as error:
            report.errors.append(f"{key}: {error}")
    # 远端有、本地没有的文档 → 下行
    for (label, kind) in remote_by_key:
        if (label, kind) in seen:
            continue
        document: LocalDocument | None = _document_for_remote(label, kind, users_dir, session_dir)
        if document is None:
            continue
        try:
            _pull(document, token, report,
                  raw=remote_raw.get((label, kind)))
        except (SyncError, OSError, ValueError) as error:
            report.errors.append(f"{label}/{kind}: {error}")


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
