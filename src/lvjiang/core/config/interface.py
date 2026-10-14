"""interface.json：界面、Profile 展示、告警和服务器状态的统一存储。"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .session import SessionStore

#: 合法的激活项 kind —— 「当前在用哪个」的选择。它们频繁变动，丢了重选一次
#: 即可，所以和界面状态一起放在 interface.json；plan 是机器级方案（图库 +
#: 环境 + 布局 + 模式的组合），env 是当前工作环境。
#: 日常脚本与展示分组独立保存在 daily 节点：本实例正在看的脚本与分组
#: （workflow_id / group）在 interface.json，脚本顺序、显示名等偏好仍在
#: session.json 的 daily.scripts。
ACTIVE_KINDS = frozenset({
    "user", "layout", "space", "env", "plan",
})


def interface_path() -> Path:
    from ... import constants
    return constants.SESSION_PATH.with_name("interface.json")


class InterfaceStore(SessionStore):
    """复用节点锁与原子写入，只读取独立界面状态文件。"""

    TRANSIENT_PATHS = frozenset({
        # 激活项整棵子树都是"本实例在用哪个"：只读实例可以临时切换，但不
        # 落盘，也不该影响持有写权限的实例。
        ("actives",),
        ("daily",),
        ("ui_state",),
        ("batch",),
        ("profile", "overview_active_group"),
    })

    @property
    def path(self) -> Path:
        return self._path_override if self._path_override is not None else interface_path()

    # ─── 激活项（actives）─────────────────────────────────

    def get_active(self, kind: str, default: Any = None) -> Any:
        """读取 ``actives.<kind>``——本实例在用哪个用户/布局/空间/环境/方案。"""
        if kind not in ACTIVE_KINDS:
            raise KeyError(f"unknown active kind: {kind}")
        with self._thread_lock:
            actives = self._data.get("actives")
            if isinstance(actives, dict) and kind in actives:
                return deepcopy(actives[kind])
            return default

    def set_active(self, kind: str, value: Any) -> None:
        """写入一个激活项。"""
        if kind not in ACTIVE_KINDS:
            raise KeyError(f"unknown active kind: {kind}")

        def _set(data: dict) -> None:
            current = data.get("actives")
            actives = deepcopy(current) if isinstance(current, dict) else {}
            actives[kind] = deepcopy(value)
            data["actives"] = actives

        self._mutate_disk(_set)


_store: InterfaceStore | None = None


def get_interface_store() -> InterfaceStore:
    global _store
    if _store is None:
        _store = InterfaceStore()
    return _store


def reset_interface_store() -> None:
    global _store
    _store = None


def initialize_state_stores() -> None:
    """启动门禁：在创建任何页面前加载运行状态。"""
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


def load_reference_grid() -> dict[str, Any]:
    """读取图库工具的网格参数，不读取 session.settings 的旧节点。"""
    value = load_ui_page_state("reference_manager").get("grid")
    return value if isinstance(value, dict) else {}


def save_reference_grid(grid: dict[str, Any]) -> None:
    """只合并网格参数，保留图库窗口大小及其他界面状态。"""
    update_ui_page_state("reference_manager", {"grid": grid})


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
