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
                effective = self._source.resolve_read(rel_path)
                if system_source.is_file():
                    target = self.resolver.system_dir / rel_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(system_source, target)
                elif effective is not None and not local_source.is_file():
                    # 远程新增/顶替在隔离会话中作为只读系统基线。
                    target = self.resolver.system_dir / rel_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(effective, target)
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
                content_version = versioning.read_version(
                    self.resolver.system_dir / rel_path)
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
