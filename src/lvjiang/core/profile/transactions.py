"""一条同步 Profile 写入链共用 SQLite 事务，提交后再派发异步事件。"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Iterator

from loguru import logger

if TYPE_CHECKING:
    import sqlite3

    from .repository import ProfileDB


class ProfileWriteRejected(ValueError):
    """同步脚本拒绝候选变化；整个当前事务必须回滚。"""


@dataclass
class ProfileTransaction:
    db: ProfileDB
    connection: sqlite3.Connection
    callbacks: list[Callable[[], object]] = field(default_factory=list)
    failure: BaseException | None = None


_current: ContextVar[ProfileTransaction | None] = ContextVar('profile_transaction', default=None)


def current_transaction() -> ProfileTransaction | None:
    return _current.get()


def after_commit(callback: Callable[[], object]) -> None:
    active = current_transaction()
    if active is None:
        callback()
    else:
        active.callbacks.append(callback)


def reject_write(message: str) -> None:
    error = ProfileWriteRejected(message)
    active = current_transaction()
    if active is not None:
        active.failure = error
    raise error


@contextmanager
def profile_transaction(db: ProfileDB) -> Iterator[ProfileTransaction]:
    active = current_transaction()
    if active is not None:
        if active.db is not db:
            raise RuntimeError('Profile 同步链不能切换数据库实例')
        try:
            yield active
        except BaseException as error:
            active.failure = error
            raise
        return
    connection = db._connect()
    state = ProfileTransaction(db, connection)
    token = _current.set(state)
    try:
        connection.execute('BEGIN IMMEDIATE')
        yield state
        if state.failure is not None:
            raise state.failure
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        _current.reset(token)
        connection.close()
    # 回调只能在真实提交后执行；失败不能反向把已提交事务标记成未提交。
    for callback in state.callbacks:
        try:
            callback()
        except Exception:  # noqa: BLE001
            logger.exception('Profile 已提交，但变更通知派发失败')
