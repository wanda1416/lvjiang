"""本地文档脏标记：写路径 bump，同步层消费，落 account.json 的 document_sync。"""
from __future__ import annotations

import json
from pathlib import Path


def _account_path() -> Path:
    from ..account.service import account_config_path
    return account_config_path()


def _state() -> dict:
    try:
        data = json.loads(_account_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    state = data.get("document_sync") if isinstance(data, dict) else None
    return dict(state) if isinstance(state, dict) else {}


def _save(state: dict) -> None:
    path = _account_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
    except (OSError, ValueError):
        return
    data["document_sync"] = state
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    except OSError:
        pass


def mark_dirty(label: str, kind: str) -> None:
    """文档被本地修改后调用；同步成功后由同步层清掉。"""
    key = f"{label}/{kind}"
    state = _state()
    dirty = state.get("dirty")
    if not isinstance(dirty, dict):
        dirty = {}
    dirty[key] = True
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
