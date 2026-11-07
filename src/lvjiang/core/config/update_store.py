"""配置更新归档与可恢复安装；不接触 local/session，不负责网络与 Qt。"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from ..fs_util import atomic_write_bytes
from ..json_files import formatted_json_bytes
from ..paths import get_config_local_dir, get_config_system_dir, get_config_updates_dir
from ..update_bundle import (
    digest,
    file_hashes,
    json_bytes,
    pack,
    parse_manifest,
    read_tree,
    series,
    tree_digest,
    unpack,
    validate_names,
    verify_archive,
)
from ..versioning import update_namespace
from .content import configuration_lock, content_changed
from .member_composition import REGISTRIES, compose


class UpdateStore:
    """明确配置根与可信预置摘要后使用。调用方负责拒绝自动更新开发源码目录。"""

    def __init__(self, config: Path, app_version: str, baseline_sha256: str):
        self.config = config.resolve()
        self.system = get_config_system_dir(self.config)
        self.updates = get_config_updates_dir(self.config)
        self.version = series(app_version)
        self.app_version = app_version
        self.namespace = update_namespace(app_version)
        self.baseline_sha256 = baseline_sha256
        self.root = self.updates / f"v{self.namespace}"
        self.state_path = self.root / "state.json"

    @contextmanager
    def locked(self, *, blocking: bool = True) -> Iterator[bool]:
        with configuration_lock(self.config, blocking=blocking) as acquired:
            yield acquired

    def _write(self, path: Path, data: bytes, *, fsync: bool = False) -> None:
        atomic_write_bytes(path, data, prefix=".update-", fsync=fsync)

    def _state(self) -> dict[str, Any]:
        path = self.state_path
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def _recover(self) -> None:
        """有日志就回滚未完成事务；只操作日志格式固定的自有临时目录。"""
        journal = self.updates / "transaction.json"
        if not journal.is_file():
            return
        data = json.loads(journal.read_text(encoding="utf-8"))
        token = data.get("token", "")
        if not isinstance(token, str) or len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
            raise ValueError("更新恢复日志损坏，请保留 updates 并恢复预置")
        previous = self.updates / f"previous-{token}"
        rejected = self.updates / f"rejected-{token}"
        if previous.is_dir():
            if self.system.exists():
                self.system.rename(rejected)
            previous.rename(self.system)
        version = update_namespace(data.get("app_version", data["app_series"]))
        self._write(self.updates / f"v{version}" / "state.json", formatted_json_bytes(data["previous_state"]))
        journal.unlink()
        shutil.rmtree(rejected, ignore_errors=True)

    def recover(self) -> None:
        with self.locked():
            self._recover()

    def _baseline(self) -> dict[str, bytes]:
        """安装底座：直接用 r0；缺失或不可读时退回当前 system。"""
        path = self.root / "r0.zip"
        if path.is_file():
            try:
                return unpack(path.read_bytes())
            except ValueError as error:
                logger.warning("r0 归档不可读，改用当前 system：{}", error)
        files = read_tree(self.system)
        if tree_digest(files) == self.baseline_sha256:
            self._write(path, pack(files))
        return files

    def _archive(self, path: Path, data: bytes) -> None:
        if not path.is_file():
            self._write(path, data)

    def cache_index(self, channel: str, raw: bytes) -> None:
        if channel not in {"public", "member"}:
            raise ValueError("更新渠道无效")
        with self.locked():
            self._write(self.root / channel / "index.json", raw)

    def _archive_directory(self, channel: str, revision: int, manifest_sha256: str) -> Path:
        """归档按清单摘要寻址；同修订重发不同内容不互相覆盖。调用方持锁。"""
        return self.root / channel / f"r{revision}" / manifest_sha256

    def cache_member(self, metadata: Mapping[str, Any], manifest: bytes, encrypted: bytes) -> None:
        if not manifest:
            raise ValueError("会员归档清单缺失")
        revision = metadata["package_version"]
        with self.locked():
            root = self._archive_directory("member", revision, digest(manifest))
            self._archive(root / "manifest.json", manifest)
            self._archive(root / "package.bin", encrypted)

    # ── 下载暂存：后台只落候选，安装留给下次启动 ────────────────────
    #
    # 归档本身就是暂存区（按修订与清单摘要寻址），另在 state.json 里登记
    # `pending`，才能让 prune_archives() 认出「还没安装但不该回收」的候选。

    def cache_public(self, manifest_json: bytes, archive: bytes) -> None:
        """暂存公开候选：按修订与清单摘要寻址，不触碰 system。"""
        if not manifest_json or not archive:
            raise ValueError("公开候选缺少清单或归档")
        manifest = parse_manifest(json.loads(manifest_json))
        revision = int(manifest["revision"])
        with self.locked():
            root = self._archive_directory("public", revision, digest(manifest_json))
            self._archive(root / "manifest.json", manifest_json)
            self._archive(root / "update.zip", archive)

    def pending(self) -> dict[str, Any]:
        """已下载、等下次启动安装的候选；空映射表示没有。"""
        pending = self._state().get("pending")
        return pending if isinstance(pending, dict) else {}

    def mark_pending(self, *, public: tuple[int, str] | None = None,
                     member: tuple[int, str] | None = None) -> None:
        """登记待安装候选；只动 state，不安装。"""
        with self.locked():
            state = self._state()
            pending = dict(state.get("pending") or {})
            if public is not None:
                pending["revision"] = int(public[0])
                pending["public_sha256"] = public[1]
            if member is not None:
                pending["member"] = {"package_version": int(member[0]),
                                     "manifest_sha256": member[1]}
            state["pending"] = pending
            self._write(self.state_path, formatted_json_bytes(state), fsync=True)

    def clear_pending(self, *, public: bool = False, member: bool = False) -> None:
        with self.locked():
            state = self._state()
            pending = dict(state.get("pending") or {})
            if public:
                pending.pop("revision", None)
                pending.pop("public_sha256", None)
            if member:
                pending.pop("member", None)
            if pending:
                state["pending"] = pending
            else:
                state.pop("pending", None)
            self._write(self.state_path, formatted_json_bytes(state), fsync=True)

    def pending_public(self) -> tuple[dict[str, Any], bytes, bytes] | None:
        """返回 ``(manifest, archive, manifest_json)``；文件缺失或摘要不符时返回 None。"""
        pending = self.pending()
        revision = int(pending.get("revision") or 0)
        sha256 = str(pending.get("public_sha256") or "")
        if not revision or not sha256:
            return None
        root = self._archive_directory("public", revision, sha256)
        manifest_path = root / "manifest.json"
        archive_path = root / "update.zip"
        if not manifest_path.is_file() or not archive_path.is_file():
            logger.warning("暂存的公开候选缺少文件，跳过离线安装：r{}", revision)
            return None
        manifest_json = manifest_path.read_bytes()
        if digest(manifest_json) != sha256:
            logger.warning("暂存的公开候选清单摘要不符，跳过离线安装：r{}", revision)
            return None
        return parse_manifest(json.loads(manifest_json)), archive_path.read_bytes(), manifest_json

    def pending_member(self) -> tuple[bytes, bytes, bytes] | None:
        """返回 ``(index_json, manifest_json, encrypted)``；文件缺失时返回 None。

        暂存只保留密文包：解密仍需授权接口返回的一次性密钥，因此安装阶段要重新
        取一次授权，而不是把明文会员内容留在磁盘上等下次启动。
        """
        pending = self.pending()
        member = pending.get("member")
        if not isinstance(member, dict):
            return None
        version = int(member.get("package_version") or 0)
        sha256 = str(member.get("manifest_sha256") or "")
        if not version or not sha256:
            return None
        root = self._archive_directory("member", version, sha256)
        index_path = self.root / "member" / "index.json"
        manifest_path = root / "manifest.json"
        package_path = root / "package.bin"
        if not all(path.is_file() for path in (index_path, manifest_path, package_path)):
            logger.warning("暂存的会员候选缺少文件，跳过离线安装：v{}", version)
            return None
        return index_path.read_bytes(), manifest_path.read_bytes(), package_path.read_bytes()

    def prune_archives(self) -> None:
        """只回收本系列未引用的完整归档，不动 r0、索引和事务资料。"""
        with self.locked():
            if (self.updates / "transaction.json").exists():
                return
            state = self._state()
            keep: dict[str, set[tuple[int, str]]] = {"public": set(), "member": set()}
            for selection in (state, state.get("previous_selection", {})):
                if selection.get("revision") and selection.get("public_sha256"):
                    keep["public"].add((selection["revision"], selection["public_sha256"]))
                member = selection.get("member") or {}
                if member.get("package_version") and member.get("manifest_sha256"):
                    keep["member"].add((member["package_version"], member["manifest_sha256"]))
            # 已下载但还没安装的候选必须保活：否则下次启动一 prune 就把它删掉，
            # 后台线程等于白下载。
            pending = state.get("pending") or {}
            if pending.get("revision") and pending.get("public_sha256"):
                keep["public"].add((int(pending["revision"]), pending["public_sha256"]))
            pending_member = pending.get("member") or {}
            if pending_member.get("package_version") and pending_member.get("manifest_sha256"):
                keep["member"].add((int(pending_member["package_version"]),
                                    pending_member["manifest_sha256"]))
            for channel, root in ((name, self.root / name) for name in keep):
                if not root.exists():
                    continue
                for revision in root.iterdir():
                    if revision.is_symlink() or not revision.is_dir() or not re.fullmatch(r"r[1-9]\d*", revision.name):
                        continue
                    for item in revision.iterdir():
                        if item.is_symlink() or not item.is_dir():
                            continue
                        manifest = item / "manifest.json"
                        if not manifest.is_file():
                            continue
                        full_digest = digest(manifest.read_bytes())
                        if (int(revision.name[1:]), full_digest) not in keep[channel]:
                            shutil.rmtree(item)

    def install(
        self, manifest: dict[str, Any] | None = None, archive: bytes | None = None, *,
        manifest_json: bytes | None = None,
        reset_public: bool = False,
        member_files: Mapping[str, bytes] | None = None,
        member_metadata: Mapping[str, Any] | None = None,
        activate: Callable[[Path, tuple[str, ...]], None],
    ) -> tuple[str, ...]:
        """主节点提交安装；消费者刷新在安装临界区外执行。"""
        pending = None

        def defer(system, changed):
            nonlocal pending
            pending = (system, changed)

        changed = self._install(
            manifest, archive, manifest_json=manifest_json,
            reset_public=reset_public, member_files=member_files, member_metadata=member_metadata,
            activate=defer)
        if pending is not None:
            try:
                activate(*pending)
            except Exception as error:
                logger.warning("配置已安装，消费者刷新失败，不回滚配置文件：{}", error)
        return changed

    def _replay(self, revision: int, state: Mapping[str, Any]) -> tuple[dict[str, Any] | None, bytes | None, bytes | None]:
        """按本地记录的修订重放已归档的公开层；0 或归档缺失时只有 r0。"""
        if not revision:
            return None, None, None
        root = self._archive_directory("public", revision, state["public_sha256"])
        if not (root / "manifest.json").is_file() or not (root / "update.zip").is_file():
            logger.warning("公开归档缺失，按 r0 重新安装：r{}", revision)
            return None, None, None
        manifest_json = (root / "manifest.json").read_bytes()
        return parse_manifest(json.loads(manifest_json)), (root / "update.zip").read_bytes(), manifest_json

    def _retained_member(self, state: Mapping[str, Any],
                         files: Mapping[str, bytes]) -> tuple[Mapping[str, Any], dict[str, bytes]]:
        """保留已安装会员内容：同系列按记录重建，文件已被删则跳过。"""
        metadata = state.get("member", {}) if state.get("app_series") == self.version else {}
        names = metadata.get("files", {})
        validate_names(names)
        fragments = metadata.get("registries", {})
        return metadata, {name: (fragments[name].encode("utf-8") if name in fragments else files[name])
                          for name in names if name in files or name in fragments}

    def _install(
        self, manifest: dict[str, Any] | None = None, archive: bytes | None = None, *,
        manifest_json: bytes | None = None,
        reset_public: bool = False,
        member_files: Mapping[str, bytes] | None = None,
        member_metadata: Mapping[str, Any] | None = None,
        activate: Callable[[Path, tuple[str, ...]], None],
    ) -> tuple[str, ...]:
        """只比较修订号：与本地记录相同不动，不同就从 r0 重建整棵树。

        member_files=None 表示保留已安装会员内容；空映射表示明确撤回。
        """
        if manifest is not None:
            manifest = parse_manifest(manifest)
            if manifest["app_series"] != self.version:
                raise ValueError("配置包与客户端系列不匹配")
        with self.locked():
            self._recover()
            previous_state = self._state()
            local_revision = int(previous_state.get("revision", 0))
            if reset_public:
                effective, archive, revision = None, None, 0
            elif manifest is not None:
                revision = int(manifest["revision"])
                if revision == local_revision and member_files is None:
                    return ()
                effective = manifest
            elif member_files is None:
                return ()
            else:
                revision = local_revision
                effective, archive, manifest_json = self._replay(revision, previous_state)
                if effective is None:
                    revision = 0
            baseline = self._baseline()
            if effective is not None:
                if archive is None:
                    raise ValueError("缺少公开增量包")
                if not manifest_json:
                    manifest_json = json_bytes(effective)
                public = verify_archive(effective, archive)
            else:
                public = {}
            deleted = set(effective["delete_files"]) if effective is not None else set()
            official = {name: data for name, data in baseline.items() if name not in deleted}
            previous_files = read_tree(self.system)
            if member_files is None:
                member_metadata, member_files = self._retained_member(previous_state, previous_files)
            members = dict(member_files)
            if not members:
                # 撤回会员：底座可能退回当前 system，里面仍含会员新增的文件。
                installed = set((previous_state.get("member") or {}).get("files", {})) - REGISTRIES
                official = {name: data for name, data in official.items() if name not in installed}
            validate_names(members)
            member_revision = int((member_metadata or {}).get("config_version", 0))
            candidate = compose({**official, **public}, members)
            validate_names(candidate)
            changed = tuple(sorted(name for name in previous_files.keys() | candidate.keys()
                                   if previous_files.get(name) != candidate.get(name)))
            public_sha256 = ""
            if revision:
                assert archive is not None and manifest_json is not None
                public_sha256 = digest(manifest_json)
                revision_dir = self._archive_directory("public", revision, public_sha256)
                self._archive(revision_dir / "update.zip", archive)
                self._archive(revision_dir / "manifest.json", manifest_json)
            token = uuid.uuid4().hex
            new_state = {"app_series": self.version, "baseline_sha256": self.baseline_sha256,
                         "member_index_revision": member_revision, "revision": revision,
                         "public_sha256": public_sha256,
                         "installed_at": datetime.now(timezone.utc).isoformat(),
                         "member": {**(member_metadata or {}), "files": file_hashes(members),
                                    "registries": {name: value.decode("utf-8") for name, value in members.items()
                                                   if name in REGISTRIES}}}
            if not changed:
                if any(new_state[key] != previous_state.get(key)
                       for key in ("revision", "public_sha256", "member", "member_index_revision")):
                    new_state["previous_selection"] = previous_state.get("previous_selection", {})
                    self._write(self.state_path, formatted_json_bytes(new_state), fsync=True)
                return ()
            owner = tempfile.TemporaryDirectory(prefix="candidate-", dir=self.updates)
            temporary = Path(owner.name)
            retired = self.updates / f"previous-{token}"
            journal = self.updates / "transaction.json"
            try:
                for name, content in candidate.items():
                    self._write(temporary / name, content)
                self._write(journal, formatted_json_bytes({"token": token, "app_series": self.version,
                                                 "app_version": self.app_version, "previous_state": previous_state}),
                            fsync=True)
                try:
                    self.system.rename(retired)
                    temporary.rename(self.system)
                    new_state["previous_selection"] = {key: previous_state.get(key) for key in
                                                        ("revision", "public_sha256", "member")}
                    self._write(self.state_path, formatted_json_bytes(new_state), fsync=True)
                    journal.unlink()
                except BaseException:
                    self._recover()
                    raise
                try:
                    from .resolver import ConfigResolver
                    content_changed(ConfigResolver(self.system, get_config_local_dir(self.config), dev_mode=False, config_dir=self.config))
                    activate(self.system, changed)
                except Exception as error:
                    logger.warning("配置已安装，消费者刷新失败，不回滚配置文件：{}", error)
                # 安装只发生在读取配置之前，此时没有任务持有 system 下的路径。
                # 只回收本事务创建的旧目录。
                try:
                    shutil.rmtree(retired)
                except OSError:
                    # 已提交的安装不因旧文件被第三方占用而被报告为失败。
                    logger.warning("配置已安装，旧目录暂被占用，保留用于手工清理: {}", retired.name)
                return changed
            finally:
                owner.cleanup()
