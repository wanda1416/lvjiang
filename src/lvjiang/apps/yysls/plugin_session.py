"""燕云插件会话配置 —— 主 session.json 的 yysls 顶层节点

插件运行期配置（如调律配置）统一存储在主 session.json 的 ``yysls`` 节点下，
与其他顶层节点（ui_state / settings / daily 等）共存。
保存采用 read-modify-write，不覆盖不识别的配置。

首次加载时自动迁移旧 ``yysls/session.json`` 数据（一次性）。
"""
from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from lvjiang.constants import SESSION_CONFIG_DIR, SESSION_PATH

# 旧路径，仅用于一次性迁移
_YYSLS_LEGACY_PATH = SESSION_CONFIG_DIR / "yysls" / "session.json"

# 插件数据在主 session.json 中的顶层 key
PLUGIN_KEY = "yysls"


class PluginSession:
    """插件会话配置：读写主 session.json 的 yysls 节点，写入即落盘"""

    def __init__(self, path: Path | None = None):
        self._path = path or SESSION_PATH
        self._data = self._load()

    # ─── 读写 ──────────────────────────────────────────

    def _load(self) -> dict:
        """从主 session.json 的 yysls 节点加载；首次自动迁移旧文件"""
        data = self._read_main()
        yysls = data.get(PLUGIN_KEY)
        if isinstance(yysls, dict) and yysls:
            return yysls
        # 无数据 → 尝试迁移旧文件
        return self._migrate()

    def _save(self):
        """read-modify-write 主 session.json，只更新 yysls 节点"""
        data = self._read_main()
        data[PLUGIN_KEY] = self._data
        self._write_main(data)

    def _read_main(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"读取主 session 失败: {e}")
        return {}

    def _write_main(self, data: dict):
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"保存主 session 失败: {e}")

    # ─── 旧文件迁移 ────────────────────────────────────

    def _migrate(self) -> dict:
        """将旧 yysls/session.json 数据迁移到主 session.json"""
        if not _YYSLS_LEGACY_PATH.exists():
            return {}
        try:
            old_data = json.loads(_YYSLS_LEGACY_PATH.read_text(encoding="utf-8"))
            if not isinstance(old_data, dict) or not old_data:
                return {}
            logger.info(f"迁移旧插件 session: {_YYSLS_LEGACY_PATH}")
            # 写入主 session.json
            main_data = self._read_main()
            main_data[PLUGIN_KEY] = old_data
            self._write_main(main_data)
            # 迁移成功后删除旧文件
            try:
                _YYSLS_LEGACY_PATH.unlink()
                # 如果 yysls 目录为空，也清理
                parent = _YYSLS_LEGACY_PATH.parent
                if parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
            except OSError:
                pass
            return old_data
        except Exception as e:
            logger.warning(f"迁移旧插件 session 失败: {e}")
        return {}

    # ─── 对外接口 ──────────────────────────────────────

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
