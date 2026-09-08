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
    lease = Lease(root / "config" / "session" / ".locks" / "configuration.lock")
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


def user_lease(username: str, users_dir: Path | None = None) -> Lease:
    if not username:
        raise ValueError("执行用户不能为空")
    if users_dir is None:
        from ..constants import USERS_DIR
        users_dir = USERS_DIR
    # User names are immutable storage keys in the current user model.
    identity = os.path.normcase(str((users_dir / f"{username}.json").resolve()))
    key = hashlib.sha256(identity.encode()).hexdigest()
    return Lease(users_dir / ".locks" / f"{key}.execution.lock")


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
