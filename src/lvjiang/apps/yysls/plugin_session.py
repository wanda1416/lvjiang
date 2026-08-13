"""燕云插件会话配置 —— 主 session.json 的 yysls 顶层节点

插件运行期配置（如调律配置）统一存储在主 session.json 的 ``yysls`` 节点下，
与其他顶层节点（ui_state / settings / daily 等）共存。
读写委托 SessionStore（锁内原子读-改-写），不会覆盖不识别的配置；
键名 PLUGIN_KEY 只存在于插件侧，核心层零感知。

首次加载时自动迁移旧 ``yysls/session.json`` 数据（一次性）。
"""
from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from lvjiang.constants import SESSION_CONFIG_DIR
from lvjiang.core.config.session import SessionStore, get_session_store

# 旧路径，仅用于一次性迁移
_YYSLS_LEGACY_PATH = SESSION_CONFIG_DIR / "yysls" / "session.json"

# 插件数据在主 session.json 中的顶层 key
PLUGIN_KEY = "yysls"


class PluginSession:
    """插件会话配置：读写主 session.json 的 yysls 节点（委托 SessionStore）

    不带 path 构造时使用全局单例 store（与其他节点共享内存态，防相互覆盖）；
    显式传 path 时构造独立 store（测试隔离）。
    """

    def __init__(self, path: Path | None = None):
        self._store = SessionStore(path) if path else get_session_store()
        self._migrate_legacy()

    # ─── 旧文件迁移 ────────────────────────────────────

    def _migrate_legacy(self):
        """将旧 yysls/session.json 数据迁移到主 session.json（一次性）"""
        node = self._store.get_node(PLUGIN_KEY)
        if isinstance(node, dict) and node:
            return  # 已有数据，跳过迁移
        if not _YYSLS_LEGACY_PATH.exists():
            return
        try:
            old_data = json.loads(_YYSLS_LEGACY_PATH.read_text(encoding="utf-8"))
            if not isinstance(old_data, dict) or not old_data:
                return
            logger.info(f"迁移旧插件 session: {_YYSLS_LEGACY_PATH}")
            self._store.set_node(PLUGIN_KEY, old_data)
            # 迁移成功后删除旧文件
            try:
                _YYSLS_LEGACY_PATH.unlink()
                # 如果 yysls 目录为空，也清理
                parent = _YYSLS_LEGACY_PATH.parent
                if parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                pass
        except Exception as e:
            logger.warning(f"迁移旧插件 session 失败: {e}")

    # ─── 对外接口 ──────────────────────────────────────

    def get_section(self, key: str) -> dict:
        """读取一节配置（不存在时返回空 dict）"""
        node = self._store.get_node(PLUGIN_KEY)
        if not isinstance(node, dict):
            return {}
        value = node.get(key)
        return value if isinstance(value, dict) else {}

    def set_section(self, key: str, value: dict):
        """写入一节配置并落盘（锁内原子读-改-写，保留插件节点其他 section）"""
        self._store.mutate_node(
            PLUGIN_KEY,
            lambda old: {**(old if isinstance(old, dict) else {}), key: value},
        )


_session: PluginSession | None = None


def get_plugin_session() -> PluginSession:
    global _session
    if _session is None:
        _session = PluginSession()
    return _session
