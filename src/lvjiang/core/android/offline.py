"""通过既有 ADB 代理通道单向下发离线配置包。"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Callable

from ..license import has_feature
from .agent import AgentClient


def require_offline_sync_access() -> None:
    """PC 向手机下发配置属于 Lv1；不约束已有配置的手机离线执行。"""
    if not has_feature("lv1"):
        raise PermissionError("向手机下发配置需要激活 Lv1，请在设置的「功能激活」中激活")


def sync_offline_bundle(
    agent: AgentClient, path: Path, *,
    cancelled: Callable[[], bool] = lambda: False,
    progress: Callable[[int, int], None] = lambda _done, _total: None,
    preserve_task_params: bool = True,
) -> dict:
    require_offline_sync_access()
    if agent.status.get("offline_protocol") != 2:
        raise RuntimeError("手机 APK 不支持新的任务参数保留协议，请先更新 APK")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    total = path.stat().st_size
    agent.call("offline_sync_begin", size=total, sha256=digest.hexdigest(),
               preserve_task_params=preserve_task_params)
    offset = 0
    with path.open("rb") as stream:
        while chunk := stream.read(192 << 10):
            if cancelled():
                raise RuntimeError("同步已取消，手机原配置尚未应用新包")
            agent.call("offline_sync_chunk", offset=offset, data=base64.b64encode(chunk).decode("ascii"))
            offset += len(chunk)
            progress(offset, total)
    if cancelled():
        raise RuntimeError("同步已取消，手机原配置尚未应用新包")
    result, _ = agent.call("offline_sync_commit", timeout=120)
    return result
