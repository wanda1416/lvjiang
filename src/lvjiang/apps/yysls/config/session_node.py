"""燕云插件会话文件，以及旧 ``session.json.yysls`` 的一次性迁移。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from loguru import logger

from lvjiang.core.config.document_store import DocumentDirectoryStore
from lvjiang.core.config.session import get_session_store

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

    def _migrate_legacy(self, legacy: Any) -> None:
        if not isinstance(legacy, dict):
            raise ValueError("session.json.yysls 必须是对象，旧数据已保留")
        unknown = sorted(set(legacy) - set(DOCUMENT_FILES))
        if unknown:
            raise ValueError("旧 yysls 含未登记子项，旧数据已保留: " + ", ".join(unknown))
        documents: dict[str, dict] = {}
        for key in DOCUMENT_FILES:
            value = legacy.get(key, {})
            if not isinstance(value, dict):
                raise ValueError(f"旧 yysls.{key} 必须是对象，旧数据已保留")
            documents[key] = value
        # 崩溃重试只接受与旧数据相同的已写文件；不同数据需要人工处理。
        for key, value in documents.items():
            path = self._path(key)
            if path.exists() and self._read(path) != value:
                raise ValueError(f"旧会话数据与新文件冲突，已保留两边: {path}")
        for key, value in documents.items():
            path = self._path(key)
            if not path.exists():
                self._write(path, value)

    def _prepare_initial_documents(self) -> None:
        migrated = get_session_store().consume_node("yysls", self._migrate_legacy)
        if migrated:
            logger.info("已将 session.json.yysls 迁移至 config/session/yysls")


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
