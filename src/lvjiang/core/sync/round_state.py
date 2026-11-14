"""一轮同步共用的云端版本行（``/api/sync/state``）。

服务端把每条通道的最新版本/水位放在**一行**上（``account_sync_version``），
``/api/sync/state`` 只读这一行。一轮同步开头取一次，后面各通道拿它与本地记下的
版本比较，没变化就整条跳过对应的拉取请求——空闲轮只有这一个请求。

- :func:`sync_round` 划出「一轮」：轮内 :func:`cloud_state` 只真正请求一次；
  轮外每次调用都直接请求（行为与原来一致）。
- 本轮里**任何写请求成功之后**调用 :func:`invalidate`：之后再要版本时重新取，
  跳过判断永远基于「最后一次写入之后」的云端状态。
- 版本比较一律用「与本地记录**不相等**」而不是「大于」：清除同步、换库会让
  云端版本归零或回退，不等就拉，宁可多拉一次也不漏。
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from .client import SyncError, post

_UNSET: dict = {}
_round: ContextVar[dict | None] = ContextVar("lvjiang_sync_round", default=None)


@contextmanager
def sync_round() -> Iterator[None]:
    """一轮同步：轮内共享一次 ``/api/sync/state`` 结果。可嵌套（内层复用外层）。"""
    if _round.get() is not None:
        yield
        return
    token = _round.set({"state": _UNSET})
    try:
        yield
    finally:
        _round.reset(token)


def cloud_state(token: str, poster=None) -> dict:
    """云端版本行。轮内缓存（直到 :func:`invalidate`），轮外每次请求。

    失败抛 :class:`SyncError`，**不缓存失败**：调用方按原有三态语义处理。
    ``poster`` 是调用方模块自己的 ``post``（便于各模块按原样替换传输层）。
    """
    current = _round.get()
    if current is not None and current.get("state") is not _UNSET:
        return current["state"]
    state = (poster or post)("/api/sync/state", {}, token)
    if not isinstance(state, dict):
        raise SyncError("同步响应格式无效")
    if current is not None:
        current["state"] = state
    return state


def invalidate() -> None:
    """本轮发生了写入：丢掉缓存的版本行，下一次 :func:`cloud_state` 重新取。"""
    current = _round.get()
    if current is not None:
        current["state"] = _UNSET


def version(state: dict, key: str):
    """取版本字段；服务端没给（老服务端）时返回 None，调用方据此照常拉取。"""
    value = state.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, str)):
        return value
    return None
