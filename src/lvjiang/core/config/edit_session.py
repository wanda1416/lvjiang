"""隔离的配置编辑会话。

编辑器在独立 ConfigResolver 上工作；提交时再通过真实 resolver 的公开 API
回放差异，避免 UI 改写全局解析器或绕过分层、版本与原子写入规则。
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import yaml

from . import versioning
from .resolver import ConfigResolver


class ConfigEditSession:
    """为一组聚合文件和实体目录提供隔离编辑及显式提交。"""

    def __init__(self, source: ConfigResolver, *, merged_paths: tuple[str, ...],
                 entity_dirs: tuple[str, ...]):
        self._source = source
        self._merged_paths = merged_paths
        self._entity_dirs = entity_dirs
        self._tmp = tempfile.TemporaryDirectory(prefix="lvjiang-config-edit-")
        root = Path(self._tmp.name)
        system = root / "system"
        local = root / "local"
        remote = root / "remote"
        system.mkdir(parents=True)
        local.mkdir()
        remote.mkdir()
        self.resolver = ConfigResolver(
            system_dir=system,
            local_dir=local,
            remote_dir=remote,
            dev_mode=source.is_dev_mode(),
        )
        self._closed = False
        self._materialize()
        self._baseline_entities = self._entity_snapshot()

    def _materialize(self) -> None:
        for rel_path in self._merged_paths:
            target = self.resolver.system_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(yaml.dump(
                self._source.load_merged(rel_path), allow_unicode=True,
                sort_keys=False), encoding="utf-8")
        for rel_dir in self._entity_dirs:
            for name in self._source.enumerate_entities(
                    rel_dir, "*.yaml", include_internal=True):
                rel_path = f"{rel_dir}/{name}"
                system_source = self._source.system_dir / rel_path
                local_source = self._source.local_dir / rel_path
                remote_source = self._source.remote_dir / rel_path
                # 临时 system 层代表真实的有效基底：remote 只有通过版本
                # 仲裁时才替换 system；local 仍作为完整影子保持最高优先级。
                # 不能只因 system 文件存在就复制它，否则会把正在生效的
                # remote 新版本降回旧 system 版本。
                base_source = (
                    remote_source
                    if self._source.remote_supersedes(rel_path)
                    else system_source
                )
                if base_source.is_file():
                    target = self.resolver.system_dir / rel_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(base_source, target)
                if local_source.is_file():
                    target = self.resolver.local_dir / rel_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(local_source, target)

    def _entity_snapshot(self) -> dict[str, bytes]:
        result: dict[str, bytes] = {}
        for rel_dir in self._entity_dirs:
            for name in self.resolver.enumerate_entities(
                    rel_dir, "*.yaml", include_internal=True):
                rel_path = f"{rel_dir}/{name}"
                path = self.resolver.resolve_read(rel_path)
                if path is not None:
                    result[rel_path] = path.read_bytes()
        return result

    @staticmethod
    def _payload_version(rel_path: str, payload: bytes | None) -> int | None:
        """读取会话快照里的版本号，不依赖文件仍存在。"""
        if payload is None:
            return None
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            return None
        return versioning.version_from_text(text, Path(rel_path).suffix)

    def commit(self) -> None:
        """通过真实 resolver 提交相对上次保存的全部差异。"""
        if self._closed:
            raise RuntimeError("配置编辑会话已经关闭")
        for rel_path in self._merged_paths:
            data = self.resolver.load_merged(rel_path)
            self._source.save_merged(rel_path, data)

        current = self._entity_snapshot()
        for rel_path in sorted(self._baseline_entities.keys() - current.keys()):
            self._source.delete_entity(rel_path)
        for rel_path, payload in current.items():
            if self._baseline_entities.get(rel_path) == payload:
                continue
            text = payload.decode("utf-8")
            content_version = None
            if self._source.is_dev_mode() and versioning.spec_for(rel_path):
                # 会话的 system 基底可能是正在生效的 remote 新版本。普通
                # 编辑必须像直接通过真实 resolver 保存一样，保留真实 system
                # 的旧版本，让 remote 继续生效；只有用户在会话里显式提升过
                # 版本时，才把新版本号一并提交到真实 system。
                draft_version = self._payload_version(rel_path, payload)
                baseline_version = self._payload_version(
                    rel_path, self._baseline_entities.get(rel_path))
                if draft_version != baseline_version:
                    content_version = draft_version
            self._source.write_entity(
                rel_path, text, content_version=content_version)
        self._baseline_entities = current

    def reset(self) -> None:
        """丢弃未提交内容，并从真实配置重新建立编辑基线。"""
        if self._closed:
            raise RuntimeError("配置编辑会话已经关闭")
        for root in (
            self.resolver.system_dir,
            self.resolver.local_dir,
            self.resolver.remote_dir,
        ):
            shutil.rmtree(root)
            root.mkdir(parents=True)
        self._materialize()
        self._baseline_entities = self._entity_snapshot()

    def close(self) -> None:
        if self._closed:
            return
        self._tmp.cleanup()
        self._closed = True


__all__ = ["ConfigEditSession"]
