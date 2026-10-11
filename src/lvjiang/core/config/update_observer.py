"""跨实例安装代次观察；不轮询解析全部配置，也不修改用户选择。"""
from __future__ import annotations

from collections.abc import Callable

from ..update_bundle import file_hashes, read_tree
from .update_store import UpdateStore


class InstallationObserver:
    def __init__(self, store: UpdateStore):
        self.store = store
        self.generation = ""
        self.files: dict[str, str] = {}
        self.accept()

    def accept(self) -> None:
        with self.store.locked(blocking=False) as acquired:
            if not acquired:
                return
            state = self.store._state()
            self.generation = state.get("generation", "")
            self.files = state.get("files") or file_hashes(read_tree(self.store.system))

    def poll(self, activate: Callable[[tuple[str, ...]], None]) -> tuple[str, ...]:
        with self.store.locked(blocking=False) as acquired:
            if not acquired:
                return ()
            if (self.store.updates / "transaction.json").exists():
                return ()
            state = self.store._state()
            generation = state.get("generation", "")
            if generation == self.generation:
                return ()
            files = state.get("files", {})
            changed = tuple(sorted(name for name in self.files.keys() | files.keys()
                                   if self.files.get(name) != files.get(name)))
            if changed:
                activate(changed)
            self.generation, self.files = generation, files
            return changed
