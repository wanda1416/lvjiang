"""本地文档变更感知：写路径立即标脏 + 磁盘戳兜底，同步层消费。

两条来源合并成同一个「本地有改动」信号：

1. **写路径标脏**（`mark_dirty`）：本进程改完文档立刻记名，最快。
2. **磁盘戳比对**（`stamps`）：记录每份文档上次同步时的 ``(size, mtime_ns)``，
   下一轮 stat 一遍就能发现「谁改的」——包括**只读实例**或外部工具改的文件。
   只做 stat，不读内容、不哈希。

状态落 ``account.json`` 的 ``document_sync``；**只读实例不写**（登录态与同步
状态只归主实例），它的改动完全靠主实例的磁盘戳比对自动感知。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from loguru import logger


def writable() -> bool:
    """本实例是否有权写同步状态（只读实例没有）。"""
    from ..access import is_readonly

    return not is_readonly()


def _account_path() -> Path:
    # 每次动态取 constants，便于测试 monkeypatch（account.service 在模块顶层
    # 绑定了常量，测试改不到）。
    from ...constants import SESSION_CONFIG_DIR
    return SESSION_CONFIG_DIR / "account.json"


def _read() -> dict:
    try:
        data = json.loads(_account_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _state() -> dict:
    state = _read().get("document_sync")
    return dict(state) if isinstance(state, dict) else {}


def _save(state: dict) -> None:
    """写回 document_sync。

    标记是尽力而为：写失败（未登录、只读实例、测试门禁）只降级为「下次多做
    一次全量对比」，绝不能让用户的一次便利贴保存因为标记写不进去而失败。
    """
    if not writable():
        return
    path = _account_path()
    data = _read()
    if not data:
        return
    data["document_sync"] = state
    try:
        tmp = path.with_name(f".{path.name}.dirty")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        os.replace(tmp, path)
    except Exception as error:      # noqa: BLE001 — 标记失败不能顶掉业务写入
        logger.debug("[sync] 脏标记写回失败（不影响本地数据）：{}", error)


def mark_dirty(label: str, kind: str) -> None:
    """文档被本进程修改后调用；同步成功后由同步层清掉。"""
    state = _state()
    dirty = state.get("dirty")
    if not isinstance(dirty, dict):
        dirty = {}
    dirty[f"{label}/{kind}"] = True
    state["dirty"] = dirty
    _save(state)


def dirty_keys() -> set[str]:
    dirty = _state().get("dirty")
    return set(dirty) if isinstance(dirty, dict) else set()


def remote_document_id() -> int:
    value = _state().get("remote_document_id")
    return value if isinstance(value, int) and value >= 0 else 0


def stamps() -> dict[str, list[int]]:
    """每份文档上次同步时的 ``[size, mtime_ns]``。"""
    raw = _state().get("stamps")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, list[int]] = {}
    for key, value in raw.items():
        if (isinstance(key, str) and isinstance(value, list) and len(value) == 2
                and all(isinstance(item, int) for item in value)):
            result[key] = value
    return result


def save_sync_state(remote_id: int, dirty: set[str],
                    stamps: dict[str, list[int]] | None = None) -> None:
    state = _state()
    state["remote_document_id"] = remote_id
    state["dirty"] = {key: True for key in dirty}
    if stamps is not None:
        state["stamps"] = stamps
    _save(state)
