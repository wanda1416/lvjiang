"""每个用户一份 ``users/<用户名>.linger.json``。

写入契约与备战方案（``LoadoutRepository``）保持一致，便于文档通道统一处理：

- 读：不创建文件、不申请写锁，浏览不该产生副作用；
- 写：``user_write_access`` + 线程锁 + ``locked_paths`` 三层串行，``revision``
  自增，原子替换，写完 ``mark_dirty(user, "linger")`` 让同步引擎知道本地变了。

``locked_paths`` 不能省：同步下行也用同一把锁，否则拉取途中落盘会被判成
「下行期间已被编辑」，白丢一次远端更新。
"""
from __future__ import annotations

import copy
import json
import os
import threading
from collections.abc import Callable
from pathlib import Path

from loguru import logger

from lvjiang.core.access import user_write_access
from lvjiang.core.fs_util import atomic_write_bytes, locked_paths
from lvjiang.core.json_files import formatted_json_bytes
from lvjiang.core.sync.dirty import mark_dirty

from .models import ItemSlot, LingerState

#: 用户级文档后缀，与 ``apps/yysls/sync/documents.py`` 登记的 kind 对应
DOCUMENT_SUFFIX = ".linger.json"
#: 文档通道使用的 kind
DOCUMENT_KIND = "linger"

_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}


def _path_lock(path: Path) -> threading.RLock:
    key = os.path.normcase(str(path.resolve()))
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


class LingerStore:
    """一个用户的记录文档读写。"""

    def __init__(self, username: str, users_dir: Path | None = None):
        name = str(username or "").strip()
        if not name or "/" in name or "\\" in name:
            raise ValueError(f"非法用户名：{username!r}")
        if users_dir is None:
            from lvjiang.constants import USERS_DIR

            users_dir = USERS_DIR
        self.username = name
        self.users_dir = Path(users_dir)
        self.path = self.users_dir / f"{name}{DOCUMENT_SUFFIX}"
        self._lock = _path_lock(self.path)

    def load(self) -> LingerState:
        """读取记录；文件不存在或损坏都返回空状态，不抛异常。"""
        with self._lock:
            try:
                raw = self.path.read_text(encoding="utf-8")
            except FileNotFoundError:
                return LingerState()
            except OSError as exc:
                logger.error(f"读取袅袅之音记录失败 {self.path}：{exc}")
                return LingerState()
        try:
            data = json.loads(raw)
        except ValueError as exc:
            logger.error(f"袅袅之音记录不是合法 JSON {self.path}：{exc}")
            return LingerState()
        return LingerState.from_dict(data)

    def set_done(self, key: str, value: int, count: int) -> LingerState:
        """写入某个条目的已得枚数；值没变时不写盘、不涨 revision。"""

        def mutate(state: LingerState) -> bool:
            return state.set_done(key, value, count)

        return self._write_if(mutate)

    def set_slots(self, key: str, slots: list[ItemSlot], count: int) -> LingerState:
        """整体写入某个条目的子项（逐个录入名字的条目用）。"""

        def mutate(state: LingerState) -> bool:
            return state.set_slots(key, slots, count)

        return self._write_if(mutate)

    def set_booked(self, key: str, value: int, done: int) -> LingerState:
        """记录「已入账多少枚」；只在账本写成功后调用。"""

        def mutate(state: LingerState) -> bool:
            return state.set_booked(key, value, done)

        return self._write_if(mutate)

    def _write_if(self, mutate: Callable[[LingerState], bool]) -> LingerState:
        with user_write_access(self.username, self.users_dir), self._lock, \
                locked_paths(self.path):
            state = self.load()
            if not mutate(state):
                return state
            state.revision += 1
            self._save(state)
            return copy.deepcopy(state)

    def _save(self, state: LingerState) -> None:
        atomic_write_bytes(
            self.path,
            formatted_json_bytes(state.to_dict()),
            prefix=f".{self.path.stem}_",
        )
        mark_dirty(self.username, DOCUMENT_KIND)
