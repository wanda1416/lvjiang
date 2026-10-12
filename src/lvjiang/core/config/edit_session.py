"""隔离的配置编辑会话。

编辑器在独立 ConfigResolver 上工作；提交时再通过真实 resolver 的公开 API
回放差异，避免 UI 改写全局解析器或绕过分层、版本与原子写入规则。
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import yaml

from .conflicts import checked_write, file_baseline
from .resolver import REGISTRY_LIST_PATHS, ConfigResolver, compute_diff, merge_doc


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
        system.mkdir(parents=True)
        local.mkdir()
        self.resolver = ConfigResolver(
            system_dir=system,
            local_dir=local,
            dev_mode=source.is_dev_mode(),
            track_content=False,
        )
        self._closed = False
        self._baseline_merged: dict[str, dict] = {}
        self._materialize()
        self._baseline_entities = self._entity_snapshot()
        self._source_baselines = self._capture_baselines()

    def _capture_baselines(self) -> dict:
        paths = set(self._merged_paths) | {name for name, _layer in self._baseline_entities}
        return {name: file_baseline(self._source, name) for name in paths}

    def _materialize(self) -> None:
        for rel_path in self._merged_paths:
            data = self._source.load_merged(rel_path)
            self._baseline_merged[rel_path] = data
            target = self.resolver.system_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(yaml.dump(
                data, allow_unicode=True,
                sort_keys=False), encoding="utf-8")
        for rel_dir in self._entity_dirs:
            for name in self._source.enumerate_entities(
                    rel_dir, "*.yaml", include_internal=True):
                rel_path = f"{rel_dir}/{name}"
                system_source = self._source.system_dir / rel_path
                local_source = self._source.local_dir / rel_path
                base_source = system_source
                if base_source.is_file():
                    target = self.resolver.system_dir / rel_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(base_source, target)
                if local_source.is_file():
                    target = self.resolver.local_dir / rel_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(local_source, target)

    def _entity_snapshot(self) -> dict[tuple[str, str], bytes]:
        """记录两个可编辑实体层；仅存生效内容会丢失保存位置。"""
        result: dict[tuple[str, str], bytes] = {}
        for rel_dir in self._entity_dirs:
            for name in self.resolver.enumerate_entities(
                    rel_dir, "*.yaml", include_internal=True):
                rel_path = f"{rel_dir}/{name}"
                for layer, root in (
                    ("system", self.resolver.system_dir),
                    ("local", self.resolver.local_dir),
                ):
                    path = root / rel_path
                    if path.is_file():
                        result[(rel_path, layer)] = path.read_bytes()
        return result

    def commit(self) -> None:
        """通过真实 resolver 提交相对上次保存的全部差异。"""
        if self._closed:
            raise RuntimeError("配置编辑会话已经关闭")
        current = self._entity_snapshot()
        changed = {name for name, layer in current.keys() | self._baseline_entities.keys()
                   if current.get((name, layer)) != self._baseline_entities.get((name, layer))}
        changed.update(name for name in self._merged_paths
                       if self.resolver.load_merged(name) != self._baseline_merged[name])
        checked_write(self._source,
                      {name: self._source_baselines.get(name, (None, None)) for name in changed},
                      self._commit)
        self._source_baselines = self._capture_baselines()

    def _commit(self) -> None:
        for rel_path in self._merged_paths:
            data = self.resolver.load_merged(rel_path)
            registry = REGISTRY_LIST_PATHS.get(rel_path, ())
            changes = compute_diff(self._baseline_merged[rel_path], data, registry)
            if changes:
                # 编辑期间官方配置或别的窗口可能更新；只提交草稿实际改过的字段。
                self._source.save_merged(rel_path, merge_doc(
                    self._source.load_merged(rel_path), changes, registry))
            self._baseline_merged[rel_path] = data

        current = self._entity_snapshot()
        # 先写目标层，再删源层；搬移过程中失败时至少保留一份文件。
        for (rel_path, layer), payload in current.items():
            if self._baseline_entities.get((rel_path, layer)) == payload:
                continue
            text = payload.decode("utf-8")
            if os.linesep == "\r\n":
                # 临时 resolver 也使用文本模式写文件：Windows 上工作副本
                # 因而已经是 CRLF。若把这段原样交给真实 resolver，它的
                # 文本模式会再次把每个 \n 扩成 CRLF，形成 \r\r\n，表现为
                # YAML 每行之间多出一个空行。真实写入前还原为逻辑换行，
                # 最终落盘时只做一次平台换行转换。
                text = text.replace("\r\n", "\n").replace("\r", "\n")
            self._source.write_entity(
                rel_path, text, layer=layer)
        for rel_path, layer in sorted(
                self._baseline_entities.keys() - current.keys()):
            self._source.delete_entity(rel_path, layer=layer)
        self._baseline_entities = current

    def reset(self) -> None:
        """丢弃未提交内容，并从真实配置重新建立编辑基线。"""
        if self._closed:
            raise RuntimeError("配置编辑会话已经关闭")
        for root in (
            self.resolver.system_dir,
            self.resolver.local_dir,
        ):
            shutil.rmtree(root)
            root.mkdir(parents=True)
        self._materialize()
        self._baseline_entities = self._entity_snapshot()
        self._source_baselines = self._capture_baselines()

    def close(self) -> None:
        if self._closed:
            return
        self._tmp.cleanup()
        self._closed = True


__all__ = ["ConfigEditSession"]
