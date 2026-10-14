"""燕云插件独立会话文件。"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from lvjiang.core.config.document_store import DocumentDirectoryStore

STORAGE_VERSION = 1
DOCUMENT_FILES = {
    "play_styles": "play_styles.json",
    "graduations": "graduations.json",
    "attr_loadout": "attr_loadout.json",
    "attr_derivations": "attr_derivations.json",
}


class YyslsSessionStore(DocumentDirectoryStore):
    """只允许访问登记过的会话文档；跨文件更新使用可恢复事务。"""

    def __init__(self, root: Path):
        super().__init__(
            root,
            DOCUMENT_FILES,
            storage_version=STORAGE_VERSION,
            label="燕云会话",
        )


_store: YyslsSessionStore | None = None


def get_store() -> YyslsSessionStore:
    global _store
    from lvjiang import constants

    root = constants.SESSION_CONFIG_DIR / "yysls"
    if _store is None or _store.root != root:
        _store = YyslsSessionStore(root)
    return _store


def initialize_session_storage() -> None:
    get_store().ensure_initialized()


def reset_session_storage() -> None:
    """清除进程内实例，供隔离测试使用。"""
    global _store
    _store = None


def load(key: str) -> dict:
    return get_store().load(key)


def mutate(key: str, fn: Callable[[dict], dict]) -> dict:
    return get_store().mutate(key, fn)


def mutate_many(
    keys: tuple[str, ...], fn: Callable[[dict[str, dict]], dict[str, dict]],
) -> dict[str, dict]:
    return get_store().mutate_many(keys, fn)
