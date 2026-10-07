"""interface.json：界面、Profile 展示、告警和服务器状态的统一存储。"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .session import SessionStore

INTERFACE_VERSION = 1
_MOVED_NODES = ("ui_state", "profile", "alert_info", "server_config")
_MIGRATION = {"source": "session", "version": INTERFACE_VERSION}


def interface_path() -> Path:
    from ... import constants
    return constants.SESSION_PATH.with_name("interface.json")


class InterfaceStore(SessionStore):
    """复用节点锁与原子写入；旧数据只在新文件不存在时导入。"""

    FORMAT_VERSION = INTERFACE_VERSION
    TRANSIENT_PATHS = frozenset({
        ("profile", "overview_active_group"),
        ("ui_state", "batch"), ("ui_state", "main_page"),
        ("ui_state", "scene_editor"), ("ui_state", "reference_manager"),
        ("ui_state", "task_history"),
    })

    def __init__(self, path: Path | str | None = None, *, legacy_path: Path | None = None):
        super().__init__(path)
        self._migrated = self._migrate(legacy_path or self.path.with_name("session.json"))

    @property
    def path(self) -> Path:
        return self._path_override if self._path_override is not None else interface_path()

    def _migrate(self, legacy_path: Path) -> bool:
        # 固定锁序 interface -> session。中间标记让崩溃后的旧节点清理可重试。
        with self._thread_lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._acquire_write_lock()
            try:
                fresh = not self.path.exists()
                current = self._read_disk(strict=True)
                pending = current.get("migration") == _MIGRATION
                if not fresh and not pending:
                    self._data = current
                    return False
                legacy = SessionStore(legacy_path)

                def transfer(data: dict) -> None:
                    nonlocal current
                    if fresh:
                        current = {
                            "version": INTERFACE_VERSION,
                            "migration": deepcopy(_MIGRATION),
                            "ui_state": deepcopy(data.get("ui_state", {})),
                            "profile": deepcopy(data.get("profile", {})),
                            "alert_info": deepcopy(data.get("alert_info", [])),
                            "server_config": deepcopy(data.get("server_config", {})),
                        }
                        profile = current["profile"]
                        current["alert_history"] = (
                            profile.pop("alert_history", {}) if isinstance(profile, dict) else {})
                        self._write_disk_atomic(current)
                    for node in _MOVED_NODES:
                        data.pop(node, None)

                # 严格读取源文件，坏文件不得被空默认值覆盖或清理。
                legacy._mutate_disk(transfer, stamp_version=False, strict=True)
                current.pop("migration", None)
                self._write_disk_atomic(current)
                self._data = deepcopy(current)
                return True
            finally:
                self._file_lock.release()


_store: InterfaceStore | None = None


def get_interface_store() -> InterfaceStore:
    global _store
    if _store is None:
        _store = InterfaceStore()
        if _store._migrated:
            from .session import get_session_store
            get_session_store().reload()
    return _store


def reset_interface_store() -> None:
    global _store
    _store = None


def initialize_state_stores() -> None:
    """启动门禁：在创建任何页面前加载运行状态并完成界面状态迁移。"""
    from .session import get_session_store
    get_session_store()
    get_interface_store()


# ─── UI 页面状态安全入口 ──────────────────────────────────

def load_ui_page_state(page_key: str) -> dict[str, Any]:
    """读取 ``ui_state.<page_key>``，非法或缺失时返回空字典。"""
    state = get_interface_store().get_node("ui_state", {})
    if not isinstance(state, dict):
        return {}
    page = state.get(page_key)
    return page if isinstance(page, dict) else {}


def update_ui_page_state(page_key: str, patch: dict[str, Any]) -> dict:
    """原子浅合并 ``ui_state.<page_key>``，保留该页面其他字段。

    这是页面级 UI 状态的唯一写入口。不得写成
    ``update_node("ui_state", {page_key: patch})``，后者会整体替换页面，
    例如保存页签索引时删除窗口大小。
    """
    if not isinstance(page_key, str) or not page_key:
        raise ValueError("page_key 必须是非空字符串")
    if not isinstance(patch, dict):
        raise TypeError("patch 必须是 dict")

    def _merge(old):
        state = dict(old) if isinstance(old, dict) else {}
        page = state.get(page_key)
        page = dict(page) if isinstance(page, dict) else {}
        page.update(patch)
        state[page_key] = page
        return state

    return get_interface_store().mutate_node("ui_state", _merge)


# ─── 便捷函数：alert_info 告警存储 ────────────────────────────


def get_alerts() -> list[dict[str, Any]]:
    """读取 interface.json 的 alert_info 节点（告警列表，最新在前）"""
    value = get_interface_store().get_node("alert_info")
    return value if isinstance(value, list) else []


def add_alert(alert_id: str, message: str, timestamp: str) -> bool:
    """追加告警到栈顶（列表头部），最新优先展示。返回 True 表示新增成功，False 表示已存在"""
    added = False

    def _mutate(current):
        nonlocal added
        alerts = current if isinstance(current, list) else []
        # 去重：同 ID 告警不重复添加
        for alert in alerts:
            if alert.get("id") == alert_id:
                return alerts
        new_alert = {"id": alert_id, "message": message, "timestamp": timestamp}
        added = True
        return ([new_alert] + alerts)[:200]  # 插入到头部，截断防无限膨胀

    get_interface_store().mutate_node("alert_info", _mutate)
    return added


def dismiss_alert(alert_id: str) -> None:
    """移除指定 ID 的告警"""
    def _mutate(current):
        alerts = current if isinstance(current, list) else []
        return [a for a in alerts if a.get("id") != alert_id]
    get_interface_store().mutate_node("alert_info", _mutate)
