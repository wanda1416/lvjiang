"""Process lifetime configuration ownership and user execution leases."""
from __future__ import annotations

import hashlib
import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path

from fasteners import InterProcessLock

from .paths import config_directory, session_directory


class AccessDeniedError(RuntimeError):
    pass


_mutex = threading.RLock()
_held: set[str] = set()
_authorized: ContextVar[frozenset[str]] = ContextVar("execution_users", default=frozenset())


class Lease:
    def __init__(self, path: Path):
        self.key = os.path.normcase(str(path.resolve()))
        self.lock = InterProcessLock(self.key)
        self.acquired = False

    def acquire(self) -> bool:
        with _mutex:
            if self.acquired or self.key in _held:
                return False
            Path(self.key).parent.mkdir(parents=True, exist_ok=True)
            if not self.lock.acquire(blocking=False):
                return False
            _held.add(self.key)
            self.acquired = True
            return True

    def release(self) -> None:
        with _mutex:
            if self.acquired:
                self.lock.release()
                self.acquired = False
                _held.discard(self.key)

    @contextmanager
    def authorized(self):
        if not self.acquired:
            raise AccessDeniedError("用户执行锁已释放")
        token = _authorized.set(_authorized.get() | {self.key})
        try:
            yield
        finally:
            _authorized.reset(token)


_instance: Lease | None = None
_readonly = False


def initialize_instance(root: Path, *, readonly: bool = False) -> None:
    global _instance, _readonly
    if _instance is not None:
        raise RuntimeError("实例权限已经初始化")
    lease = Lease(session_directory(config_directory(root)) / ".locks" / "configuration.lock")
    _readonly = True  # Fail closed if lock acquisition raises.
    if not readonly:
        _readonly = not lease.acquire()
    _instance = lease


def close_instance() -> None:
    global _instance
    if _instance is not None:
        _instance.release()
        _instance = None


def is_readonly() -> bool:
    return _readonly


def require_primary_write() -> None:
    """共享配置只有主节点可以落盘；只读选择由界面存储留在内存。"""
    if is_readonly():
        raise AccessDeniedError("只读节点不能修改共享配置，请在主节点操作")


@contextmanager
def user_write_access(username: str, users_dir: Path | None = None):
    """复用用户占用门禁，不创建数据文件锁。

    同主节点内的 UI/任务共用已有占用；只读节点必须在任务授权上下文中。
    主节点修改未占用用户时短暂取得同一门禁，不能撞上其它节点的用户任务。
    """
    lease = user_lease(username, users_dir)
    # 检查与写回共用现有占用状态互斥；任务不能在写回中途释放/抢占用户。
    with _mutex:
        if lease.key in _held and (not is_readonly() or lease.key in _authorized.get()):
            yield
            return
        if is_readonly():
            raise AccessDeniedError("只读节点只能写入本节点正在执行任务的用户数据")
        lease = acquire_user(username, users_dir)
        try:
            with lease.authorized():
                yield
        finally:
            lease.release()


def user_lease(username: str, users_dir: Path | None = None) -> Lease:
    if not username:
        raise ValueError("执行用户不能为空")
    if users_dir is None:
        from ..constants import USERS_DIR
        users_dir = USERS_DIR
    # User names are immutable storage keys in the current user model.
    identity = os.path.normcase(str((users_dir / f"{username}.json").resolve()))
    key = hashlib.sha256(identity.encode()).hexdigest()
    return Lease(users_dir / ".lock" / f"{key}.execution.lock")


def acquire_user(username: str, users_dir: Path | None = None) -> Lease:
    lease = user_lease(username, users_dir)
    if not lease.acquire():
        raise AccessDeniedError(f"用户「{username}」正在执行任务，请稍后重试")
    return lease


def user_execution(method):
    """Engine fallback for user-bound tools; outer launch leases remain authoritative."""
    @wraps(method)
    def guarded(self, *args, **kwargs):
        if not self.run_username:
            return method(self, *args, **kwargs)
        directory = getattr(self.session, "users_dir", None)
        lease = user_lease(self.run_username, directory)
        if lease.key in _authorized.get() and lease.key in _held:
            return method(self, *args, **kwargs)
        lease = acquire_user(self.run_username, directory)
        try:
            with lease.authorized():
                return method(self, *args, **kwargs)
        finally:
            lease.release()
    return guarded
