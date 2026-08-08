"""档案总览会话数据存储

统一管理 session.json 中 profile 节点的所有读写，包括：
- overview_groups: 总览分组配置 {group_name: {"columns": [key, ...]}}
- overview_active_group: 当前活跃分组名
- alert_history: 提醒去重记录 {alert_key: timestamp}

所有调用方必须通过本模块的函数访问 profile 节点，禁止直接 get_node/set_node。
"""

from __future__ import annotations

import threading
from typing import Any

from lvjiang.core.config import get_session_store

# session.json 中的顶层 key
_PROFILE_KEY = "profile"

# profile 节点内的子 key
_SUB_GROUPS = "overview_groups"
_SUB_ACTIVE_GROUP = "overview_active_group"
_SUB_ALERT_HISTORY = "alert_history"

# 模块级锁，保证 read-modify-write 原子性
_rw_lock = threading.Lock()


def _load() -> dict[str, Any]:
    """加载 profile 节点（深拷贝）"""
    data = get_session_store().get_node(_PROFILE_KEY, {})
    return data if isinstance(data, dict) else {}


def _save(data: dict[str, Any]) -> None:
    """整体替换 profile 节点"""
    get_session_store().set_node(_PROFILE_KEY, data)


# ─── 分组配置 ────────────────────────────────────────────────


def get_groups() -> dict:
    """获取总览分组配置 {group_name: {"columns": [key, ...]}}"""
    return _load().get(_SUB_GROUPS, {})


def save_groups(groups: dict) -> None:
    """保存总览分组配置"""
    with _rw_lock:
        data = _load()
        data[_SUB_GROUPS] = groups
        _save(data)


# ─── 活跃分组 ────────────────────────────────────────────────


def get_active_group() -> str:
    """获取当前活跃分组名"""
    return _load().get(_SUB_ACTIVE_GROUP, "")


def set_active_group(name: str) -> None:
    """设置当前活跃分组名"""
    with _rw_lock:
        data = _load()
        data[_SUB_ACTIVE_GROUP] = name
        _save(data)


# ─── 提醒历史 ────────────────────────────────────────────────


def get_alert_history() -> dict[str, str]:
    """获取提醒去重历史 {alert_key: timestamp}"""
    history = _load().get(_SUB_ALERT_HISTORY, {})
    return history if isinstance(history, dict) else {}


def set_alert_history(history: dict[str, str]) -> None:
    """整体替换提醒历史"""
    with _rw_lock:
        data = _load()
        data[_SUB_ALERT_HISTORY] = history
        _save(data)


def mark_alert(alert_key: str, timestamp: str) -> None:
    """标记一个提醒已发送"""
    with _rw_lock:
        data = _load()
        history = data.get(_SUB_ALERT_HISTORY, {})
        if not isinstance(history, dict):
            history = {}
        history[alert_key] = timestamp
        data[_SUB_ALERT_HISTORY] = history
        _save(data)


def is_alert_marked(alert_key: str) -> bool:
    """检查提醒是否已标记过"""
    return alert_key in get_alert_history()


# ─── 迁移 ────────────────────────────────────────────────────


def migrate_from_legacy() -> None:
    """从旧格式迁移到新格式

    旧格式：session.json 顶层有 profile_overview_* 和 profile_alert_history
    新格式：统一归入 profile 节点
    """
    store = get_session_store()

    # 检查是否已有新格式
    existing = store.get_node(_PROFILE_KEY, None)
    if existing and isinstance(existing, dict) and _SUB_GROUPS in existing:
        # 已有新格式，只需清理旧 key
        _cleanup_legacy_keys(store)
        return

    # 尝试从旧格式读取
    old_groups = store.get_node("profile_overview_groups", None)
    old_active = store.get_node("profile_overview_active_group", None)
    old_alerts = store.get_node("profile_alert_history", None)
    old_columns = store.get_node("profile_overview_columns", None)

    # 保留已有 profile 节点数据，合并旧 key
    base = existing if isinstance(existing, dict) else {}

    if old_groups and isinstance(old_groups, dict):
        base[_SUB_GROUPS] = old_groups
    elif old_columns and isinstance(old_columns, list):
        # 从更旧的扁平列表格式迁移
        base.setdefault(_SUB_GROUPS, {"默认": {"columns": old_columns}})
        base.setdefault(_SUB_ACTIVE_GROUP, "默认")
    else:
        base.setdefault(_SUB_GROUPS, {"默认": {"columns": []}})
        base.setdefault(_SUB_ACTIVE_GROUP, "默认")

    if old_active and isinstance(old_active, str):
        base[_SUB_ACTIVE_GROUP] = old_active

    if old_alerts and isinstance(old_alerts, dict):
        base[_SUB_ALERT_HISTORY] = old_alerts

    _save(base)
    _cleanup_legacy_keys(store)


def _cleanup_legacy_keys(store) -> None:
    """清理旧的顶层 key"""
    for key in [
        "profile_overview_columns",
        "profile_overview_groups",
        "profile_overview_active_group",
        "profile_alert_history",
    ]:
        store.delete_node(key)
