"""档案总览会话数据存储

统一管理 interface.json 中 profile 与根节点 alert_history 的读写，包括：
- overview_groups: 总览分组配置 {group_name: {"columns": [key, ...]}}
- overview_group_order: 总览分组的拖动顺序，未记录时沿用分组配置顺序
- overview_active_group: 当前活跃分组名
- 根节点 alert_history: 提醒去重记录 {alert_key: timestamp}

所有调用方必须通过本模块的函数访问这些节点，禁止直接 get_node/set_node。

多进程安全：使用 SessionStore.mutate_node() 提供的文件锁机制。
不再使用进程内锁（threading.Lock），因为其在多进程场景中无效。
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any

from lvjiang.core.config import get_interface_store

# interface.json 中的顶层 key
_PROFILE_KEY = "profile"

# profile 节点内的子 key
_SUB_GROUPS = "overview_groups"
_SUB_GROUP_ORDER = "overview_group_order"
_SUB_ACTIVE_GROUP = "overview_active_group"
_ALERT_HISTORY_KEY = "alert_history"


def _load() -> dict[str, Any]:
    """加载 profile 节点（深拷贝）"""
    data = get_interface_store().get_node(_PROFILE_KEY, {})
    return data if isinstance(data, dict) else {}


# ─── 分组配置 ────────────────────────────────────────────────


def get_groups() -> dict:
    """获取总览分组配置 {group_name: {"columns": [key, ...]}}"""
    return _ordered_groups(_load())


def _ordered_groups(data: dict) -> dict:
    groups = data.get(_SUB_GROUPS, {})
    ordered = {
        name: groups[name] for name in data.get(_SUB_GROUP_ORDER, [])
        if name in groups
    }
    ordered.update(groups)
    return ordered


def _mutate_groups(mutator) -> None:
    """只供下列显式分组/列编辑命令复用的原子更新入口。"""
    def _merge(old):
        data = old if isinstance(old, dict) else {}
        current = data.get(_SUB_GROUPS, {})
        groups = deepcopy(_ordered_groups(data)) if isinstance(current, dict) else {}
        mutator(groups)
        data[_SUB_GROUPS] = groups
        # dict 相等不比较顺序；显式列表确保仅排序也会被 SessionStore 落盘。
        data[_SUB_GROUP_ORDER] = list(groups)
        return data

    get_interface_store().mutate_node(_PROFILE_KEY, _merge)


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


def reorder_overview_groups(ordered_names: list[str]) -> None:
    """重排已展示分组，保留其他进程新建的分组及所有分组内容。"""
    def _reorder(groups: dict) -> None:
        reordered = {name: groups[name] for name in ordered_names if name in groups}
        reordered.update(groups)
        groups.clear()
        groups.update(reordered)

    _mutate_groups(_reorder)


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


def insert_overview_columns(
    group_name: str, after_key: str | None, keys: list[str],
) -> list[str]:
    """按稳定原列定位，原子添加多个字段；返回实际新增字段的顺序。"""
    inserted: list[str] = []

    def _insert(groups: dict) -> None:
        group = groups.setdefault(group_name, {"columns": []})
        columns = list(group.get("columns", []))
        if after_key is not None and after_key not in columns:
            raise ValueError("原列已被移除，请重新选择插入位置")
        index = columns.index(after_key) + 1 if after_key is not None else 0
        inserted.extend(key for key in dict.fromkeys(keys) if key not in columns)
        columns[index:index] = inserted
        group["columns"] = columns

    _mutate_groups(_insert)
    return inserted


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

    get_interface_store().mutate_node(_PROFILE_KEY, _merge)


# ─── 提醒历史 ────────────────────────────────────────────────


def get_alert_history() -> dict[str, str]:
    """获取提醒去重历史 {alert_key: timestamp}"""
    history = get_interface_store().get_node(_ALERT_HISTORY_KEY)
    return history if isinstance(history, dict) else {}


def set_alert_history(history: dict[str, str]) -> None:
    """整体替换提醒历史（多进程安全）"""
    get_interface_store().mutate_node(
        _ALERT_HISTORY_KEY, lambda _: dict(history))


def mark_alert(alert_key: str, timestamp: str) -> None:
    """标记一个提醒已发送（多进程安全）"""
    def _merge(old):
        history = old if isinstance(old, dict) else {}
        history[alert_key] = timestamp
        return history

    get_interface_store().mutate_node(_ALERT_HISTORY_KEY, _merge)


def is_alert_marked(alert_key: str) -> bool:
    """检查提醒是否已标记过"""
    return alert_key in get_alert_history()


def unmark_alert(alert_key: str) -> None:
    """移除一个提醒标记（条件不满足时调用，允许下次重新触发）（多进程安全）"""
    def _merge(old):
        history = old if isinstance(old, dict) else {}
        history.pop(alert_key, None)
        return history

    get_interface_store().mutate_node(_ALERT_HISTORY_KEY, _merge)
