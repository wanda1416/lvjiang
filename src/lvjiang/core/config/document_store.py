"""带版本和事务恢复的多文档 JSON 目录存储。"""
from __future__ import annotations

import json
import threading
from copy import deepcopy
from pathlib import Path
from typing import Callable, Mapping

from fasteners import InterProcessLock
from loguru import logger

from lvjiang.core.fs_util import atomic_write_text


class DocumentDirectoryStore:
    """将登记过的对象文档存入独立 JSON 文件。

    单文档写入使用原子替换；跨文档写入先落事务日志，下次访问时完成恢复。
    子类可重写 :meth:`_prepare_initial_documents`，在首次初始化时迁入旧数据。
    """

    LOCK_TIMEOUT = 5
    TRANSACTION_VERSION = 1

    def __init__(
        self,
        root: Path,
        documents: Mapping[str, str],
        *,
        storage_version: int = 1,
        label: str = "会话",
    ) -> None:
        self.root = root
        self.documents = dict(documents)
        self.storage_version = storage_version
        self.label = label
        self._thread_lock = threading.RLock()
        self._file_lock = InterProcessLock(str(root / ".lock"))
        self._initialized = False

    def _path(self, key: str) -> Path:
        try:
            return self.root / self.documents[key]
        except KeyError:
            raise KeyError(f"未登记的{self.label}文档: {key}") from None

    @property
    def _meta_path(self) -> Path:
        return self.root / "_meta.json"

    @property
    def _transaction_path(self) -> Path:
        return self.root / "_transaction.json"

    def _acquire(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if not self._file_lock.acquire(blocking=True, timeout=self.LOCK_TIMEOUT):
            raise TimeoutError(f"无法获取{self.label}目录锁: {self.root}")

    def _read(self, path: Path) -> dict:
        if not path.exists():
            raise FileNotFoundError(f"{self.label}文件缺失: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"{self.label}文件根节点必须是对象: {path}")
        return data

    @staticmethod
    def _write(path: Path, data: dict) -> None:
        atomic_write_text(
            path,
            json.dumps(data, ensure_ascii=False, indent=2),
            prefix=f".{path.stem}_",
        )

    def _prepare_initial_documents(self) -> None:
        """首次创建存储时迁入外部数据；默认无需处理。"""

    def _recover_unlocked(self) -> None:
        path = self._transaction_path
        if not path.exists():
            return
        transaction = self._read(path)
        documents = transaction.get("documents")
        if (
            transaction.get("version") != self.TRANSACTION_VERSION
            or not isinstance(documents, dict)
        ):
            raise ValueError(f"无法恢复{self.label}事务: {path}")
        if not documents or set(documents) - set(self.documents):
            raise ValueError(f"{self.label}事务包含未知文档: {path}")
        if any(not isinstance(value, dict) for value in documents.values()):
            raise ValueError(f"{self.label}事务包含非对象文档: {path}")
        for key, value in documents.items():
            self._write(self._path(key), value)
        path.unlink()
        logger.info("已恢复未完成的{}文件事务", self.label)

    def _initialize_unlocked(self) -> None:
        if self._meta_path.exists():
            version = self._read(self._meta_path).get("version")
            if version != self.storage_version:
                raise ValueError(f"不支持的{self.label}存储版本: {version!r}")
            self._recover_unlocked()
            for key in self.documents:
                self._read(self._path(key))
            return
        self._prepare_initial_documents()
        for key in self.documents:
            path = self._path(key)
            if not path.exists():
                self._write(path, {})
            else:
                self._read(path)
        self._recover_unlocked()
        self._write(self._meta_path, {"version": self.storage_version})

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
                    raise TypeError(f"{self.label}文档 {key} 必须是对象")
                self._write(self._path(key), updated)
                return deepcopy(updated)
            finally:
                self._file_lock.release()

    def mutate_many(
        self,
        keys: tuple[str, ...],
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
                self._write(
                    self._transaction_path,
                    {"version": self.TRANSACTION_VERSION, "documents": result},
                )
                for key, value in result.items():
                    self._write(self._path(key), value)
                self._transaction_path.unlink()
                return deepcopy(result)
            finally:
                self._file_lock.release()
