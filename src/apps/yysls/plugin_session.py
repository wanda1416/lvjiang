"""燕云插件会话配置 —— config/local/yysls/session.json

插件专属的运行期配置（如调律配置）独立于主流程 session.json 维护，
按节（顶层 dict key）读写，写入即落盘。
"""
from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from src.constants import LOCAL_CONFIG_DIR

YYSLS_LOCAL_DIR = LOCAL_CONFIG_DIR / "yysls"
YYSLS_SESSION_PATH = YYSLS_LOCAL_DIR / "session.json"


class PluginSession:
    """插件会话配置：按节读写，写入即落盘"""

    def __init__(self, path: Path | None = None):
        self._path = path or YYSLS_SESSION_PATH
        self._data = self._load()

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"加载插件 session 失败: {e}")
        return {}

    def _save(self):
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"保存插件 session 失败: {e}")

    def get_section(self, key: str) -> dict:
        """读取一节配置（不存在时返回空 dict）"""
        value = self._data.get(key)
        return value if isinstance(value, dict) else {}

    def set_section(self, key: str, value: dict):
        """写入一节配置并落盘"""
        self._data[key] = value
        self._save()


_session: PluginSession | None = None


def get_plugin_session() -> PluginSession:
    global _session
    if _session is None:
        _session = PluginSession()
    return _session
