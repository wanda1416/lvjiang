"""经已有代理通道回传历史；失败重试按运行 ID 和附件摘要去重。"""
from __future__ import annotations

import base64
import hashlib
import tempfile
from collections.abc import Callable
from pathlib import Path

from ..access import require_primary_write
from ..history_transfer import MAX_BYTES, import_history
from .agent import AgentClient


def pull_history(
    agent: AgentClient, root: Path, selected: list[str], *,
    cancelled: Callable[[], bool] = lambda: False,
    progress: Callable[[int, int], None] = lambda _done, _total: None,
) -> dict:
    require_primary_write()
    if agent.status.get("history_protocol") != 1:
        raise RuntimeError("请先更新 APK：手机不支持历史回传")
    summary, _ = agent.call("history_begin", selected=selected, timeout=120)
    token = summary["token"]
    try:
        total = summary["size"]
        if not isinstance(total, int) or not 0 < total <= MAX_BYTES:
            raise ValueError("历史包大小非法")
        directory = root / "config/session/history-transfer"
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="incoming-", dir=directory) as temp:
            archive = Path(temp) / "history.zip"
            offset = 0
            digest = hashlib.sha256()
            with archive.open("wb") as stream:
                while offset < total:
                    if cancelled():
                        raise RuntimeError("回传已取消，PC 历史尚未导入")
                    response, _ = agent.call("history_chunk", token=token, offset=offset)
                    chunk = base64.b64decode(response["data"], validate=True)
                    if response.get("offset") != offset or not chunk or len(chunk) > min(192 << 10, total - offset):
                        raise ValueError("历史分块不完整或位置错误")
                    stream.write(chunk)
                    digest.update(chunk)
                    offset += len(chunk)
                    progress(offset, total)
            if digest.hexdigest() != summary["sha256"]:
                raise ValueError("历史包摘要校验失败")
            if cancelled():
                raise RuntimeError("回传已取消，PC 历史尚未导入")
            return import_history(root, archive, selected)
    finally:
        try:
            agent.call("history_end", token=token)
        except Exception:
            pass  # 只清理冻结的传输包；下一次导出也会替换它，不删除运行原件。
