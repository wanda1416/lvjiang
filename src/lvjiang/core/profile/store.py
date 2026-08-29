"""档案总览会话数据存储

统一管理 session.json 中 profile 节点的所有读写，包括：
- overview_groups: 总览分组配置 {group_name: {"columns": [key, ...]}}
- overview_active_group: 当前活跃分组名
- alert_history: 提醒去重记录 {alert_key: timestamp}

所有调用方必须通过本模块的函数访问 profile 节点，禁止直接 get_node/set_node。

多进程安全：使用 SessionStore.mutate_node() 提供的文件锁机制。
不再使用进程内锁（threading.Lock），因为其在多进程场景中无效。
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any

from lvjiang.core.config import get_session_store

# session.json 中的顶层 key
_PROFILE_KEY = "profile"

# profile 节点内的子 key
_SUB_GROUPS = "overview_groups"
_SUB_ACTIVE_GROUP = "overview_active_group"
_SUB_ALERT_HISTORY = "alert_history"


def _load() -> dict[str, Any]:
    """加载 profile 节点（深拷贝）"""
    data = get_session_store().get_node(_PROFILE_KEY, {})
    return data if isinstance(data, dict) else {}


# ─── 分组配置 ────────────────────────────────────────────────


def get_groups() -> dict:
    """获取总览分组配置 {group_name: {"columns": [key, ...]}}"""
    return _load().get(_SUB_GROUPS, {})


def _mutate_groups(mutator) -> None:
    """只供下列显式分组/列编辑命令复用的原子更新入口。"""
    def _merge(old):
        data = old if isinstance(old, dict) else {}
        current = data.get(_SUB_GROUPS, {})
        groups = deepcopy(current) if isinstance(current, dict) else {}
        mutator(groups)
        data[_SUB_GROUPS] = groups
        return data

    get_session_store().mutate_node(_PROFILE_KEY, _merge)


def create_overview_group(name: str) -> None:
    """新增分组。"""
    def _create(groups: dict) -> None:
        if name not in groups:
            groups[name] = {"columns": []}

    _mutate_groups(_create)


def rename_overview_group(old_name: str, new_name: str) -> None:
    """重命名分组并保持分组顺序。"""
    def _rename(groups: dict) -> None:
        if old_name not in groups or new_name in groups:
            return
        renamed = {
            new_name if key == old_name else key: value
            for key, value in groups.items()
        }
        groups.clear()
        groups.update(renamed)

    _mutate_groups(_rename)


def remove_overview_group(name: str) -> None:
    """删除分组。至少保留一个分组的交互约束由 UI 负责。"""
    _mutate_groups(lambda groups: groups.pop(name, None))


def insert_overview_column(group_name: str, index: int, key: str) -> None:
    """在分组的指定位置插入列；编辑临时“默认”分组时同时完成落盘。"""
    def _insert(groups: dict) -> None:
        group = groups.setdefault(group_name, {"columns": []})
        columns = list(group.get("columns", []))
        if key in columns:
            return
        columns.insert(max(0, min(index, len(columns))), key)
        group["columns"] = columns

    _mutate_groups(_insert)


def remove_overview_column(group_name: str, key: str) -> None:
    """按 key 删除列，避免 UI 可见下标误删其他配置。"""
    def _remove(groups: dict) -> None:
        group = groups.get(group_name)
        if not isinstance(group, dict):
            return
        columns = list(group.get("columns", []))
        if key in columns:
            columns.remove(key)
            group["columns"] = columns

    _mutate_groups(_remove)


def replace_overview_column(group_name: str, old_key: str, new_key: str) -> None:
    """替换一列的字段。"""
    def _replace(groups: dict) -> None:
        group = groups.get(group_name)
        if not isinstance(group, dict):
            return
        columns = list(group.get("columns", []))
        if old_key not in columns or (new_key in columns and new_key != old_key):
            return
        columns[columns.index(old_key)] = new_key
        group["columns"] = columns

    _mutate_groups(_replace)


def reorder_overview_columns(group_name: str, ordered_keys: list[str]) -> None:
    """只允许重排列：传入集合与原配置不同则拒绝，禁止借排序增删列。"""
    def _reorder(groups: dict) -> None:
        group = groups.get(group_name)
        if not isinstance(group, dict):
            return
        columns = list(group.get("columns", []))
        if Counter(columns) != Counter(ordered_keys):
            raise ValueError("reorder_overview_columns cannot add or remove columns")
        group["columns"] = list(ordered_keys)

    _mutate_groups(_reorder)


# ─── 活跃分组 ────────────────────────────────────────────────


def get_active_group() -> str:
    """获取当前活跃分组名"""
    return _load().get(_SUB_ACTIVE_GROUP, "")


def set_active_group(name: str) -> None:
    """设置当前活跃分组名（多进程安全）"""
    def _merge(old):
        data = old if isinstance(old, dict) else {}
        data[_SUB_ACTIVE_GROUP] = name
        return data

    get_session_store().mutate_node(_PROFILE_KEY, _merge)


# ─── 提醒历史 ────────────────────────────────────────────────


def get_alert_history() -> dict[str, str]:
    """获取提醒去重历史 {alert_key: timestamp}"""
    history = _load().get(_SUB_ALERT_HISTORY, {})
    return history if isinstance(history, dict) else {}


def set_alert_history(history: dict[str, str]) -> None:
    """整体替换提醒历史（多进程安全）"""
    def _merge(old):
        data = old if isinstance(old, dict) else {}
        data[_SUB_ALERT_HISTORY] = history
        return data

    get_session_store().mutate_node(_PROFILE_KEY, _merge)


def mark_alert(alert_key: str, timestamp: str) -> None:
    """标记一个提醒已发送（多进程安全）"""
    def _merge(old):
        data = old if isinstance(old, dict) else {}
        history = data.get(_SUB_ALERT_HISTORY, {})
        if not isinstance(history, dict):
            history = {}
        history[alert_key] = timestamp
        data[_SUB_ALERT_HISTORY] = history
        return data

    get_session_store().mutate_node(_PROFILE_KEY, _merge)


def is_alert_marked(alert_key: str) -> bool:
    """检查提醒是否已标记过"""
    return alert_key in get_alert_history()


def unmark_alert(alert_key: str) -> None:
    """移除一个提醒标记（条件不满足时调用，允许下次重新触发）（多进程安全）"""
    def _merge(old):
        data = old if isinstance(old, dict) else {}
        history = data.get(_SUB_ALERT_HISTORY, {})
        if not isinstance(history, dict):
            return data  # 无需修改
        if alert_key in history:
            del history[alert_key]
            data[_SUB_ALERT_HISTORY] = history
        return data

    get_session_store().mutate_node(_PROFILE_KEY, _merge)

