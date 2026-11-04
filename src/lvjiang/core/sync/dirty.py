"""本地文档脏标记：写路径 bump，同步层消费，落 account.json 的 document_sync。"""
from __future__ import annotations

import json
import os
from pathlib import Path

from loguru import logger


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
    """文档被本地修改后调用；同步成功后由同步层清掉。"""
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


def save_sync_state(remote_id: int, dirty: set[str]) -> None:
    state = _state()
    state["remote_document_id"] = remote_id
    state["dirty"] = {key: True for key in dirty}
    _save(state)
