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
from pathlib import Path, PurePosixPath
from typing import Any

from ..fs_util import atomic_write_bytes
from ..json_files import formatted_json_bytes
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
        self.system = self.config / "system"
        self.updates = self.config / "updates"
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

    def _write(self, path: Path, data: bytes) -> None:
        atomic_write_bytes(path, data, prefix=".update-", fsync=True)

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
        # rejected 保留用于诊断；不递归删除来源不明目录。

    def recover(self) -> None:
        with self.locked():
            self._recover()

    def _baseline(self) -> dict[str, bytes]:
        path = self.root / "r0.zip"
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
                raise ValueError(f"归档摘要对应不同内容: {path.name}")
            return
        self._write(path, data)

    def cache_index(self, channel: str, raw: bytes) -> None:
        if channel not in {"public", "member"}:
            raise ValueError("更新渠道无效")
        with self.locked():
            self._write(self.root / channel / "index.json", raw)

    def _archive_directory(self, channel: str, revision: int, manifest_sha256: str) -> Path:
        """短名称仅用于定位；归档身份以清单内容的完整摘要为准。调用方持锁。"""
        parent = self.root / channel / f"r{revision}"
        target = parent / manifest_sha256[:8]
        matched = None
        if parent.is_dir():
            for item in parent.iterdir():
                if item.is_symlink() or not item.is_dir():
                    continue
                manifest = item / "manifest.json"
                if manifest.is_file() and digest(manifest.read_bytes()) == manifest_sha256:
                    matched = item
                    break
        if matched is not None:
            return matched
        # 同修订允许重新发布，短名称碰撞不能覆盖另一份完整归档。
        while target.exists():
            target = parent / uuid.uuid4().hex[:8]
        return target

    def cache_member(self, metadata: Mapping[str, Any], manifest: bytes, encrypted: bytes) -> None:
        if not manifest or digest(manifest) != metadata.get("manifest_sha256"):
            raise ValueError("会员归档清单摘要不匹配")
        if digest(encrypted) != metadata.get("sha256"):
            raise ValueError("会员归档密文摘要不匹配")
        revision = metadata["package_version"]
        with self.locked():
            root = self._archive_directory("member", revision, digest(manifest))
            self._archive(root / "manifest.json", manifest)
            self._archive(root / "package.bin", encrypted)

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
            # 未成功安装的线上目标也保留，避免其他实例回收待安装候选。
            from ..update_bundle import parse_index
            from .protected_remote import parse_manifest as parse_member
            from .protected_remote import select_package
            for channel in keep:
                index_path = self.root / channel / "index.json"
                if index_path.is_file():
                    doc = json.loads(index_path.read_bytes())
                    if channel == "public":
                        entry = parse_index(doc)["series"].get(self.version)
                        if entry:
                            keep[channel].add((entry["revision"], entry["sha256"]))
                    else:
                        package = select_package(parse_member(doc), self.app_version)
                        if package:
                            keep[channel].add((package["config_version"], digest(json_bytes(package))))
                root = self.root / channel
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
        index_snapshots: Mapping[str, bytes] | None = None,
        reset_public: bool = False,
        member_files: Mapping[str, bytes] | None = None,
        member_metadata: Mapping[str, Any] | None = None,
        preview: bool = False,
        retain_candidate: Callable[[tempfile.TemporaryDirectory[str]], None] | None = None,
        prepared: tempfile.TemporaryDirectory[str] | None = None,
        activate: Callable[[Path, tuple[str, ...]], None],
    ) -> tuple[str, ...]:
        """核验包完整性后提交文件；消费者刷新失败不能撤销已提交安装。

        member_files=None 表示保留已安装会员内容；空映射表示明确撤回。
        授权和会员清单检查由调用方完成，不能把网络失败转换为空映射。
        """
        if manifest is not None:
            parse_manifest(manifest)
            if manifest["app_series"] != self.version or manifest["baseline_sha256"] != self.baseline_sha256:
                raise ValueError("配置包与客户端系列或预置基底不匹配")
        with self.locked():
            for channel, expected in (index_snapshots or {}).items():
                if channel not in {"public", "member"} or (self.root / channel / "index.json").read_bytes() != expected:
                    raise ValueError("更新索引已被其他同步改变，请重新同步")
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
                    root = self._archive_directory("public", revision, previous_state["public_sha256"])
                    manifest_json = (root / "manifest.json").read_bytes()
                    if digest(manifest_json) != previous_state["public_sha256"]:
                        raise ValueError("已安装公开归档清单摘要不匹配")
                    manifest = parse_manifest(json.loads(manifest_json))
                    archive = (root / "update.zip").read_bytes()
                else:
                    archive = pack({})
                    manifest = {"app_series": self.version, "revision": 0,
                                "baseline_sha256": self.baseline_sha256,
                                "archive_sha256": digest(archive), "files": {}, "delete_files": []}
            else:
                manifest = parse_manifest(manifest)
            if archive is None:
                raise ValueError("缺少公开增量包")
            if manifest["app_series"] != self.version or manifest["baseline_sha256"] != self.baseline_sha256:
                raise ValueError("配置包与客户端系列或预置基底不匹配")
            public = verify_archive(manifest, archive)
            manifest_json = manifest_json if manifest_json is not None else json_bytes(manifest)
            if json.loads(manifest_json) != manifest:
                raise ValueError("公开归档清单与安装清单不一致")
            public_sha256 = digest(manifest_json) if manifest["revision"] else ""
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
            required_hash = (member_metadata or {}).get("public_manifests", {}).get(self.version)
            dependency_hash = (public_sha256 if manifest["revision"] == required_revision else
                               manifest.get("ancestors", {}).get(str(required_revision)))
            if members and required_hash and required_hash != dependency_hash:
                raise ValueError("会员包依赖的公开清单摘要与当前选择不一致")
            old_member = previous_state.get("member", {}) if installed_matches else {}
            member_revision = previous_state.get("member_index_revision", old_member.get("config_version", 0)) if installed_matches else 0
            if member_metadata and "config_version" in member_metadata:
                member_revision = member_metadata["config_version"]
            validate_names(members)
            deleted = set(manifest["delete_files"])
            directories = {str(parent) for name in baseline for parent in PurePosixPath(name).parents}
            if deleted & directories:
                raise ValueError("删除指令不能指向目录")
            official = {name: data for name, data in baseline.items() if name not in deleted}
            candidate = compose({**official, **public}, members)
            validate_names(candidate)
            changed = tuple(sorted(name for name in previous_files.keys() | candidate.keys()
                                   if previous_files.get(name) != candidate.get(name)))
            if manifest["revision"]:
                revision_dir = self._archive_directory("public", manifest['revision'], public_sha256)
                self._archive(revision_dir / "update.zip", archive)
                self._archive(revision_dir / "manifest.json", manifest_json)
            token = uuid.uuid4().hex
            new_state = {"app_series": self.version, "baseline_sha256": self.baseline_sha256,
                         "member_index_revision": member_revision,
                         "revision": manifest["revision"], "files": file_hashes(candidate),
                         "public_sha256": public_sha256,
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
                       for key in ("app_series", "baseline_sha256", "revision", "public_sha256", "files", "member", "member_index_revision")):
                    new_state["previous_selection"] = {key: previous_state.get(key) for key in
                                                        ("revision", "public_sha256", "member")}
                    self._write(self.state_path, formatted_json_bytes(new_state))
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
                if preview:
                    if retain_candidate is not None:
                        retain_candidate(owner)
                        retained = True
                    return changed
                self._write(journal, formatted_json_bytes({"token": token, "app_series": self.version,
                                                 "app_version": self.app_version, "previous_state": previous_state}))
                try:
                    self.system.rename(retired)
                    temporary.rename(self.system)
                    new_state["previous_selection"] = {key: previous_state.get(key) for key in
                                                        ("revision", "public_sha256", "member")}
                    self._write(self.state_path, formatted_json_bytes(new_state))
                    journal.unlink()
                except BaseException:
                    self._recover()
                    raise
                try:
                    from .resolver import ConfigResolver
                    content_changed(ConfigResolver(self.system, self.config / "local", dev_mode=False))
                    activate(self.system, changed)
                except Exception as error:
                    from loguru import logger
                    logger.warning("配置已安装，消费者刷新失败，不回滚配置文件：{}", error)
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
