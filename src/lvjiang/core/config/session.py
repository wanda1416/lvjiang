"""SessionStore —— session.json 的唯一读写咽喉

纯运行态配置（config/session/session.json）统一经本模块读写：
- 全量内存缓存：首次访问懒加载，此后所有读操作命中内存
- 线程安全：RLock 保护全部读写，UI 主线程与工作流后台线程并发安全
- 写即落盘：每次变更立即原子落盘（tmp + os.replace），杜绝半截文件
- 单写者快照：所有写入都基于同一份内存态 read-modify-write，
  根除多入口各自读盘再写回造成的相互覆盖

节点语义：session.json 顶层 key 即节点（ui_state / daily / settings /
active_layout / active_space 等），各调用方只操作自己的节点。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from loguru import logger


class SessionStore:
    """session.json 唯一读写入口

    不带参构造时路径动态取自 constants.SESSION_PATH（monkeypatch 友好）；
    测试可显式传入 tmp_path 构造隔离实例。
    """

    def __init__(self, path: Path | str | None = None):
        self._path_override = Path(path) if path else None
        self._lock = threading.RLock()
        self._data: dict = self._read_disk()  # 构造时立即加载

    # ─── 路径与加载 ──────────────────────────────────────

    @property
    def path(self) -> Path:
        if self._path_override is not None:
            return self._path_override
        from ... import constants
        return constants.SESSION_PATH

    def _read_disk(self) -> dict:
        path = self.path
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as e:  # noqa: BLE001 损坏文件不应阻断启动
            logger.error(f"session.json 解析失败，按空配置处理: {path}: {e}")
            return {}

    def _flush(self):
        """原子落盘：tmp 文件 + os.replace（调用方须已持锁）"""
        path = self.path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                dir=str(path.parent), prefix=".session_", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except Exception as e:  # noqa: BLE001 落盘失败仅告警，不阻断业务
            logger.warning(f"session.json 落盘失败: {path}: {e}")

    # ─── 节点读写 ────────────────────────────────────────

    def get_node(self, key: str, default: Any = None) -> Any:
        """读顶层节点（返回深拷贝，调用方改不坏内部态）"""
        with self._lock:
            value = self._data.get(key)
            return deepcopy(value) if value is not None else default

    def set_node(self, key: str, value: Any):
        """整节点替换并落盘"""
        with self._lock:
            self._data[key] = value
            self._flush()

    def update_node(self, key: str, patch: dict):
        """dict 节点一级浅合并并落盘（多组件分写同一节点用）

        节点缺失或不是 dict 时视为空 dict 重建。
        """
        with self._lock:
            node = self._data.get(key)
            node = node if isinstance(node, dict) else {}
            node.update(patch)
            self._data[key] = node
            self._flush()

    def mutate_node(self, key: str, fn: Callable[[Any], Any]) -> Any:
        """锁内原子读-改-写：fn(旧值) 的返回值作为新节点并落盘

        返回写入的新值。供需要 get+set 原子性的调用方（如插件节点）。
        """
        with self._lock:
            new_value = fn(self._data.get(key))
            self._data[key] = new_value
            self._flush()
            return new_value

    def delete_node(self, key: str):
        """删除顶层节点并落盘（不存在时静默）"""
        with self._lock:
            if key in self._data:
                del self._data[key]
                self._flush()

    def reload(self):
        """重新读盘，刷新内存态"""
        with self._lock:
            self._data = self._read_disk()


# ─── 模块级单例 ──────────────────────────────────────────

_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore()
    return _store


def reset_session_store() -> None:
    """丢弃模块级单例（测试用：monkeypatch SESSION_PATH 后避免内存态跨用例残留）"""
    global _store
    _store = None


# ─── 便捷函数：settings / material_grid ────────────────────

def load_settings() -> dict[str, Any]:
    """读取 session.json 的 settings 节点"""
    value = get_session_store().get_node("settings")
    return value if isinstance(value, dict) else {}


def save_settings(settings: dict[str, Any]) -> None:
    """保存配置到 session.json 的 settings 节点"""
    get_session_store().set_node("settings", settings)


def load_material_grid() -> dict[str, Any]:
    """读取 session.json 的 material_grid 节点"""
    value = get_session_store().get_node("material_grid")
    return value if isinstance(value, dict) else {}


def save_material_grid(grid: dict[str, Any]) -> None:
    """保存材料网格参数到 session.json 的 material_grid 节点"""
    get_session_store().set_node("material_grid", grid)
