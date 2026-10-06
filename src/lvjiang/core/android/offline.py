"""通过既有 ADB 代理通道单向下发离线配置包。"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Callable

from .agent import AgentClient


def sync_offline_bundle(
    agent: AgentClient, path: Path, *,
    cancelled: Callable[[], bool] = lambda: False,
    progress: Callable[[int, int], None] = lambda _done, _total: None,
) -> dict:
    if agent.status.get("offline_protocol") != 1:
        raise RuntimeError("手机 APK 尚不支持离线同步，请先更新 APK")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    total = path.stat().st_size
    agent.call("offline_sync_begin", size=total, sha256=digest.hexdigest())
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
