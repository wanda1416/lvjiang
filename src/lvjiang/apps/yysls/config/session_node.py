"""燕云插件会话文件，以及旧 ``session.json.yysls`` 的一次性迁移。"""
from __future__ import annotations

import json
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from fasteners import InterProcessLock
from loguru import logger

from lvjiang.core.config.session import get_session_store
from lvjiang.core.fs_util import atomic_write_text

STORAGE_VERSION = 1
DOCUMENT_FILES = {
    "play_styles": "play_styles.json",
    "graduations": "graduations.json",
    "attr_loadout": "attr_loadout.json",
    "attr_derivations": "attr_derivations.json",
}


class YyslsSessionStore:
    """只允许访问登记过的会话文档；跨文件更新使用可恢复事务。"""

    LOCK_TIMEOUT = 5

    def __init__(self, root: Path):
        self.root = root
        self._thread_lock = threading.RLock()
        self._file_lock = InterProcessLock(str(root / ".lock"))
        self._initialized = False

    def _path(self, key: str) -> Path:
        try:
            return self.root / DOCUMENT_FILES[key]
        except KeyError:
            raise KeyError(f"未登记的燕云会话文档: {key}") from None

    @property
    def _meta_path(self) -> Path:
        return self.root / "_meta.json"

    @property
    def _transaction_path(self) -> Path:
        return self.root / "_transaction.json"

    def _acquire(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if not self._file_lock.acquire(blocking=True, timeout=self.LOCK_TIMEOUT):
            raise TimeoutError(f"无法获取燕云会话目录锁: {self.root}")

    @staticmethod
    def _read(path: Path) -> dict:
        if not path.exists():
            raise FileNotFoundError(f"燕云会话文件缺失: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"会话文件根节点必须是对象: {path}")
        return data

    @staticmethod
    def _write(path: Path, data: dict) -> None:
        atomic_write_text(
            path, json.dumps(data, ensure_ascii=False, indent=2),
            prefix=f".{path.stem}_",
        )

    def _migrate_legacy(self, legacy: Any) -> None:
        if not isinstance(legacy, dict):
            raise ValueError("session.json.yysls 必须是对象，旧数据已保留")
        unknown = sorted(set(legacy) - set(DOCUMENT_FILES))
        if unknown:
            raise ValueError("旧 yysls 含未登记子项，旧数据已保留: " + ", ".join(unknown))
        documents: dict[str, dict] = {}
        for key in DOCUMENT_FILES:
            value = legacy.get(key, {})
            if not isinstance(value, dict):
                raise ValueError(f"旧 yysls.{key} 必须是对象，旧数据已保留")
            documents[key] = value
        # 崩溃重试只接受与旧数据相同的已写文件；不同数据需要人工处理。
        for key, value in documents.items():
            path = self._path(key)
            if path.exists() and self._read(path) != value:
                raise ValueError(f"旧会话数据与新文件冲突，已保留两边: {path}")
        for key, value in documents.items():
            path = self._path(key)
            if not path.exists():
                self._write(path, value)

    def _recover_unlocked(self) -> None:
        path = self._transaction_path
        if not path.exists():
            return
        transaction = self._read(path)
        documents = transaction.get("documents")
        if transaction.get("version") != 1 or not isinstance(documents, dict):
            raise ValueError(f"无法恢复燕云会话事务: {path}")
        if not documents or set(documents) - set(DOCUMENT_FILES):
            raise ValueError(f"燕云会话事务包含未知文档: {path}")
        if any(not isinstance(value, dict) for value in documents.values()):
            raise ValueError(f"燕云会话事务包含非对象文档: {path}")
        for key, value in documents.items():
            self._write(self._path(key), value)
        path.unlink()
        logger.info("已恢复未完成的燕云会话文件事务")

    def _initialize_unlocked(self) -> None:
        if self._meta_path.exists():
            version = self._read(self._meta_path).get("version")
            if version != STORAGE_VERSION:
                raise ValueError(f"不支持的燕云会话存储版本: {version!r}")
            self._recover_unlocked()
            for key in DOCUMENT_FILES:
                self._read(self._path(key))
            return
        migrated = get_session_store().consume_node("yysls", self._migrate_legacy)
        for key in DOCUMENT_FILES:
            path = self._path(key)
            if not path.exists():
                self._write(path, {})
            else:
                self._read(path)
        self._recover_unlocked()
        self._write(self._meta_path, {"version": STORAGE_VERSION})
        if migrated:
            logger.info("已将 session.json.yysls 迁移至 config/session/yysls")

    def ensure_initialized(self) -> None:
        with self._thread_lock:
            if self._initialized:
                return
            self._acquire()
            try:
                self._initialize_unlocked()
                self._initialized = True
            finally:
                self._file_lock.release()

    def load(self, key: str) -> dict:
        self._path(key)
        self.ensure_initialized()
        with self._thread_lock:
            self._acquire()
            try:
                self._recover_unlocked()
                return deepcopy(self._read(self._path(key)))
            finally:
                self._file_lock.release()

    def mutate(self, key: str, fn: Callable[[dict], dict]) -> dict:
        self._path(key)
        self.ensure_initialized()
        with self._thread_lock:
            self._acquire()
            try:
                self._recover_unlocked()
                updated = fn(deepcopy(self._read(self._path(key))))
                if not isinstance(updated, dict):
                    raise TypeError(f"燕云会话文档 {key} 必须是对象")
                self._write(self._path(key), updated)
                return deepcopy(updated)
            finally:
                self._file_lock.release()

    def mutate_many(
        self, keys: tuple[str, ...],
        fn: Callable[[dict[str, dict]], dict[str, dict]],
    ) -> dict[str, dict]:
        if not keys or len(set(keys)) != len(keys):
            raise ValueError("mutate_many 需要无重复的文档列表")
        for key in keys:
            self._path(key)
        self.ensure_initialized()
        with self._thread_lock:
            self._acquire()
            try:
                self._recover_unlocked()
                current = {key: self._read(self._path(key)) for key in keys}
                updated = fn(deepcopy(current))
                if set(updated) != set(keys) or any(
                    not isinstance(value, dict) for value in updated.values()
                ):
                    raise ValueError("mutate_many 必须返回全部请求的对象文档")
                result = {key: deepcopy(updated[key]) for key in keys}
                self._write(self._transaction_path, {"version": 1, "documents": result})
                for key, value in result.items():
                    self._write(self._path(key), value)
                self._transaction_path.unlink()
                return deepcopy(result)
            finally:
                self._file_lock.release()


_store: YyslsSessionStore | None = None


def get_store() -> YyslsSessionStore:
    global _store
    from lvjiang import constants

    root = constants.SESSION_CONFIG_DIR / "yysls"
    if _store is None or _store.root != root:
        _store = YyslsSessionStore(root)
    return _store


def initialize_session_storage() -> None:
    get_store().ensure_initialized()


def reset_session_storage() -> None:
    """清除进程内实例，供隔离测试使用。"""
    global _store
    _store = None


def load(key: str) -> dict:
    return get_store().load(key)


def mutate(key: str, fn: Callable[[dict], dict]) -> dict:
    return get_store().mutate(key, fn)


def mutate_many(
    keys: tuple[str, ...], fn: Callable[[dict[str, dict]], dict[str, dict]],
) -> dict[str, dict]:
    return get_store().mutate_many(keys, fn)
