"""User execution leases: 同一用户不被多实例互相覆盖。

实例级主节点/只读语义已移除：共享配置由 content_version 基线与覆盖确认保护，
任务数据由这里的用户占用锁保护。"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path

from fasteners import InterProcessLock

from .fs_util import lock_file
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
                # fasteners 获取失败时不会关闭它刚打开的文件句柄；每次被占用
                # 都会漏一个，长跑或严格告警环境下会累积并报 ResourceWarning。
                self._close_lockfile()
                return False
            _held.add(self.key)
            self.acquired = True
            return True

    def _close_lockfile(self) -> None:
        lockfile = getattr(self.lock, "lockfile", None)
        if lockfile is None:
            return
        try:
            lockfile.close()
        finally:
            self.lock.lockfile = None

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
    """初始化实例身份：抢占式——谁先拿到实例锁谁是主节点。

    抢不到的实例成为只读节点，它只放弃全局热键与 MCP 服务，**不锁任何功能
    入口**（更新安装、离线导入、脚本编辑、出装等照常可用）。

    ``readonly=True``（来自 ``--read-only``）直接声明只读：不参与抢占，
    把锁留给真正要干活的实例。
    """
    global _instance, _readonly
    if _instance is not None:
        raise RuntimeError("实例权限已经初始化")
    lease = Lease(lock_file(
        "instance", os.path.normcase(str(session_directory(config_directory(root)).resolve()))))
    # 抢锁失败或异常时按只读处理（fail closed）
    _readonly = True
    if not readonly:
        _readonly = not lease.acquire()
    _instance = lease


def close_instance() -> None:
    global _instance
    if _instance is not None:
        _instance.release()
        _instance = None


def is_readonly() -> bool:
    """是否只读节点：决定让不让出全局热键与 MCP，不限制任何功能入口。"""
    return _readonly


@contextmanager
def user_write_access(username: str, users_dir: Path | None = None):
    """复用用户占用门禁，不创建数据文件锁。

    同主实例内的 UI/任务共用已有占用；主实例修改未占用用户时短暂取得同一
    门禁，不能撞上其它实例的用户任务。

    界面偏好（``set_user_ui_state``）不属于任务数据，不走这里：它只锁用户
    资料文件本身，否则另一个进程跑任务时点一下筛选就会变成致命错误。
    """
    lease = user_lease(username, users_dir)
    # 检查与写回共用现有占用状态互斥；任务不能在写回中途释放/抢占用户。
    with _mutex:
        if lease.key in _held:
            yield
            return
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
    return Lease(lock_file("execution", identity))


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
