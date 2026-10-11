"""配置更新归档与可恢复安装；不接触 local/session，不负责网络与 Qt。"""
from __future__ import annotations

import json
import shutil
import tempfile
import threading
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fasteners import InterProcessLock

from ..fs_util import atomic_write_bytes
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
from .member_composition import REGISTRIES, compose

_MUTEXES: dict[Path, Any] = {}


class UpdateStore:
    """明确配置根与可信预置摘要后使用。调用方负责拒绝自动更新开发源码目录。"""

    def __init__(self, config: Path, app_version: str, baseline_sha256: str):
        self.config = config.resolve()
        self.system = self.config / "system"
        self.updates = self.config / "updates"
        self.version = series(app_version)
        self.baseline_sha256 = baseline_sha256

    @contextmanager
    def locked(self, *, blocking: bool = True) -> Iterator[bool]:
        self.updates.mkdir(parents=True, exist_ok=True)
        mutex = _MUTEXES.setdefault(self.config, threading.RLock())
        if not mutex.acquire(blocking=blocking):
            yield False
            return
        lease = InterProcessLock(self.updates / "install.lock")
        acquired = False
        try:
            acquired = lease.acquire(blocking=blocking)
            yield acquired
        finally:
            if acquired:
                lease.release()
            mutex.release()

    def _write(self, path: Path, data: bytes) -> None:
        atomic_write_bytes(path, data, prefix=".update-", fsync=True)

    def _state(self) -> dict[str, Any]:
        path = self.updates / "state.json"
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
        self._write(self.updates / "state.json", json_bytes(data["previous_state"]))
        journal.unlink()
        # rejected 保留用于诊断；不递归删除来源不明目录。

    def recover(self) -> None:
        with self.locked():
            self._recover()

    def _baseline(self) -> dict[str, bytes]:
        path = self.updates / f"v{self.version}" / "r0.zip"
        if path.is_file():
            files = unpack(path.read_bytes())
        else:
            files = read_tree(self.system)
        if tree_digest(files) != self.baseline_sha256:
            raise ValueError("无法确认原始预置基底，请恢复与应用版本匹配的 system")
        if not path.exists():
            self._write(path, pack(files))
        return files

    def baseline(self) -> dict[str, bytes]:
        with self.locked():
            self._recover()
            return self._baseline()

    def _archive(self, path: Path, data: bytes) -> None:
        if path.exists():
            if path.read_bytes() != data:
                raise ValueError(f"同一修订出现不同内容: {path.name}")
            return
        self._write(path, data)

    def install(
        self, manifest: dict[str, Any] | None = None, archive: bytes | None = None, *,
        reset_public: bool = False,
        member_files: Mapping[str, bytes] | None = None,
        member_metadata: Mapping[str, Any] | None = None,
        preview: bool = False,
        retain_candidate: Callable[[tempfile.TemporaryDirectory[str]], None] | None = None,
        prepared: tempfile.TemporaryDirectory[str] | None = None,
        validate: Callable[[Path], None],
        activate: Callable[[Path, tuple[str, ...]], None],
    ) -> tuple[str, ...]:
        """候选校验后换盘及内存；activate 失败时恢复并再次通知旧内存视图。

        member_files=None 表示保留已安装会员内容；空映射表示明确撤回。
        授权和会员清单检查由调用方完成，不能把网络失败转换为空映射。
        """
        if manifest is not None:
            parse_manifest(manifest)
            if manifest["app_series"] != self.version or manifest["baseline_sha256"] != self.baseline_sha256:
                raise ValueError("配置包与客户端系列或预置基底不匹配")
        with self.locked():
            self._recover()
            baseline = self._baseline()
            previous_state = self._state()
            previous_files = read_tree(self.system)
            same_series = previous_state.get("app_series") == self.version
            installed_matches = same_series and previous_state.get("files") == file_hashes(previous_files)
            # 手工恢复 r0 后，外部 state 不再代表实际已安装内容。
            if not installed_matches and tree_digest(previous_files) != self.baseline_sha256:
                raise ValueError("system 与安装记录不一致，请先恢复预置或核对外部修改")
            if manifest is None:
                revision = previous_state.get("revision", 0) if installed_matches and not reset_public else 0
                if revision:
                    root = self.updates / f"v{self.version}"
                    manifest = parse_manifest(json.loads((root / f"r{revision}.json").read_bytes()))
                    archive = (root / f"r{revision}.zip").read_bytes()
                else:
                    archive = pack({})
                    manifest = {"app_series": self.version, "revision": 0,
                                "baseline_sha256": self.baseline_sha256,
                                "archive_sha256": digest(archive), "files": {}}
            else:
                manifest = parse_manifest(manifest)
            if archive is None:
                raise ValueError("缺少公开增量包")
            if manifest["app_series"] != self.version or manifest["baseline_sha256"] != self.baseline_sha256:
                raise ValueError("配置包与客户端系列或预置基底不匹配")
            public = verify_archive(manifest, archive)
            if installed_matches and not reset_public and manifest["revision"] < previous_state.get("revision", 0):
                raise ValueError("拒绝回退的公开更新索引")
            if member_files is None:
                member_metadata = previous_state.get("member", {}) if installed_matches else {}
                names = member_metadata.get("files", {})
                validate_names(names)
                fragments = member_metadata.get("registries", {})
                member_files = {name: (fragments[name].encode("utf-8") if name in fragments
                                       else previous_files[name]) for name in names}
            members = dict(member_files)
            required_revision = (member_metadata or {}).get("public_revisions", {}).get(self.version, 0)
            if members and manifest["revision"] < required_revision:
                raise ValueError(f"会员包要求公开配置至少 r{required_revision}，请先获取对应公开更新")
            old_member = previous_state.get("member", {}) if installed_matches else {}
            member_revision = previous_state.get("member_index_revision", old_member.get("config_version", 0)) if installed_matches else 0
            if member_metadata and "config_version" in member_metadata:
                if member_metadata["config_version"] < member_revision:
                    raise ValueError("拒绝回退的会员更新索引")
                member_revision = member_metadata["config_version"]
            validate_names(members)
            candidate = compose({**baseline, **public}, members)
            validate_names(candidate)
            changed = tuple(sorted(name for name in previous_files.keys() | candidate.keys()
                                   if previous_files.get(name) != candidate.get(name)))
            revision_dir = self.updates / f"v{self.version}"
            if manifest["revision"]:
                self._archive(revision_dir / f"r{manifest['revision']}.zip", archive)
                self._archive(revision_dir / f"r{manifest['revision']}.json", json_bytes(manifest))
            token = uuid.uuid4().hex
            new_state = {"app_series": self.version, "baseline_sha256": self.baseline_sha256,
                         "member_index_revision": member_revision,
                         "revision": manifest["revision"], "files": file_hashes(candidate),
                         "generation": token, "installed_at": datetime.now(timezone.utc).isoformat(),
                         "member": {**(member_metadata or {}), "files": file_hashes(members),
                                    "registries": {name: value.decode("utf-8") for name, value in members.items()
                                                   if name in REGISTRIES}}}
            if not changed:
                if prepared is not None:
                    prepared.cleanup()
                if preview:
                    return ()
                if any(new_state[key] != previous_state.get(key)
                       for key in ("app_series", "baseline_sha256", "revision", "files", "member", "member_index_revision")):
                    self._write(self.updates / "state.json", json_bytes(new_state))
                return ()
            owner = prepared or tempfile.TemporaryDirectory(prefix="candidate-", dir=self.updates)
            temporary = Path(owner.name)
            retained = False
            retired = self.updates / f"previous-{token}"
            journal = self.updates / "transaction.json"
            try:
                if prepared is None:
                    for name, content in candidate.items():
                        self._write(temporary / name, content)
                elif file_hashes(read_tree(temporary)) != new_state["files"]:
                    raise ValueError("已准备配置与当前安装基底或候选不一致，请重新同步")
                validate(temporary)
                if preview:
                    if retain_candidate is not None:
                        retain_candidate(owner)
                        retained = True
                    return changed
                self._write(journal, json_bytes({"token": token, "previous_state": previous_state}))
                try:
                    self.system.rename(retired)
                    temporary.rename(self.system)
                    activate(self.system, changed)
                    self._write(self.updates / "state.json", json_bytes(new_state))
                    journal.unlink()
                except BaseException:
                    self._recover()
                    activate(self.system, changed)
                    raise
                # 任务持有独立快照，不引用被替换的目录。只回收本事务创建的旧目录。
                try:
                    shutil.rmtree(retired)
                except OSError:
                    # 已提交的安装不因旧文件被第三方占用而被报告为失败。
                    from loguru import logger
                    logger.warning("配置已安装，旧目录暂被占用，保留用于手工清理: {}", retired.name)
                return changed
            finally:
                if not retained:
                    owner.cleanup()
