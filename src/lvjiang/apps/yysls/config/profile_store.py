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



