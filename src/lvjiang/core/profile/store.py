"""档案总览的结构访问层

分组、列顺序、合计行与列宽统一落在 ``config/session/profile.json``（见
:mod:`lvjiang.core.profile.layout`）；本模块把那份结构包装成 UI 与脚本熟悉的
形态，并继续负责根节点 ``alert_history`` 的读写：

- 总览分组：``{group_name: {"columns": [key, ...], "show_totals": bool}}``
- 分组顺序：文件里的 ``group_order``；未记录时沿用分组字典顺序
- 活跃分组：留在 ``interface.json`` 的 ``ui_state.profile_active_group``
  （本机视图状态，不进可同步的结构文件）
- 根节点 alert_history：提醒去重记录 ``{alert_key: timestamp}``

所有调用方必须通过这些函数访问结构，禁止自己拼 JSON 路径。

多进程安全：结构文件由 ``layout`` 的跨进程锁串行化读改写，``interface.json``
由 :class:`SessionStore` 的锁串行化。
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from lvjiang.core.config import get_interface_store

from . import layout
from .layout import GroupLayout

_ALERT_HISTORY_KEY = "alert_history"

#: 活跃分组留在 interface.json 的键名（本机视图状态）。
_ACTIVE_GROUP_KEY = "profile_active_group"


# ─── 分组配置 ────────────────────────────────────────────────


def get_groups() -> dict:
    """获取总览分组配置 ``{group_name: {"columns": [...], "show_totals": bool}}``。

    返回顺序即展示顺序；键在定义里已不存在的记录照旧保留（渲染层自行隐藏），
    不在这里做「看起来无效就删除」的清理。
    """
    return layout.group_snapshot()


def _mutate_groups(mutator) -> None:
    """只供下列显式分组/列编辑命令复用的原子更新入口。

    ``layout`` 侧用 :class:`GroupLayout` 存结构，这里负责把 UI 习惯的
    ``{"columns", "show_totals"}`` 字典与之间来回翻译：列宽等字段由结构层
    自己保留，字典形式的写入不会把它们冲掉。
    """

    def _apply(groups: dict[str, GroupLayout]) -> None:
        plain = {name: group.as_store_dict() for name, group in groups.items()}
        mutator(plain)
        rebuilt: dict[str, GroupLayout] = {}
        for name, data in plain.items():
            if not isinstance(name, str) or not isinstance(data, dict):
                continue
            previous = groups.get(name) or GroupLayout()
            raw_columns = data.get("columns")
            columns = (
                [key for key in raw_columns if isinstance(key, str)]
                if isinstance(raw_columns, list)
                else []
            )
            rebuilt[name] = GroupLayout(
                columns=columns,
                show_totals=data.get("show_totals") is True,
                name_column_width=previous.name_column_width,
                # 列被删掉后它的宽度也没有意义，顺手清掉，避免文件无限膨胀。
                column_widths={
                    key: width
                    for key, width in previous.column_widths.items()
                    if key in columns
                },
            )
        groups.clear()
        groups.update(rebuilt)

    layout.mutate_groups(_apply)


def create_overview_group(name: str) -> None:
    """新增分组。"""

    def _create(groups: dict[str, GroupLayout]) -> None:
        if name not in groups:
            groups[name] = GroupLayout()

    layout.mutate_groups(_create)


def rename_overview_group(old_name: str, new_name: str) -> None:
    """重命名分组并保持分组顺序。

    直接搬 ``GroupLayout`` 对象：列宽挂在对象上，改名不该丢列宽。走「字典
    形态再重建」的适配层反而会把它当成新分组。
    """

    def _rename(groups: dict[str, GroupLayout]) -> None:
        if old_name not in groups or new_name in groups:
            return
        renamed = {
            new_name if key == old_name else key: value for key, value in groups.items()
        }
        groups.clear()
        groups.update(renamed)

    layout.mutate_groups(_rename)


def remove_overview_group(name: str) -> None:
    """删除分组。至少保留一个分组的交互约束由 UI 负责。"""

    def _remove(groups: dict[str, GroupLayout]) -> None:
        groups.pop(name, None)

    layout.mutate_groups(_remove)


def reorder_overview_groups(ordered_names: list[str]) -> None:
    """重排已展示分组，保留其他进程新建的分组及所有分组内容。"""

    def _reorder(groups: dict[str, GroupLayout]) -> None:
        reordered = {name: groups[name] for name in ordered_names if name in groups}
        reordered.update(groups)
        groups.clear()
        groups.update(reordered)

    layout.mutate_groups(_reorder)


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
    """替换一列的字段；列宽跟着走到新 key 上（换字段不该重置宽度）。"""

    def _replace(groups: dict[str, GroupLayout]) -> None:
        group = groups.get(group_name)
        if group is None:
            return
        columns = list(group.columns)
        if old_key not in columns or (new_key in columns and new_key != old_key):
            return
        columns[columns.index(old_key)] = new_key
        group.columns = columns
        if old_key != new_key:
            width = group.column_widths.pop(old_key, 0)
            if width > 0:
                group.column_widths[new_key] = width

    layout.mutate_groups(_replace)


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


# ─── 列宽 ──────────────────────────────────────────────────


def get_group_widths(group_name: str) -> tuple[int, dict[str, int]]:
    """返回该分组记录的 ``(用户名列宽, {列 key: 宽度})``。"""
    return layout.group_widths(group_name)


def set_group_widths(group_name: str, name_column_width: int, widths: dict[str, int]) -> None:
    """写入该分组列宽；用户名列宽对所有分组生效。"""
    layout.set_group_widths(
        group_name, name_column_width=name_column_width, widths=widths
    )


# ─── 合计行 ────────────────────────────────────────────────


def get_overview_totals(group_name: str) -> bool:
    """该分组的总览表是否展示合计行；从未记录过时默认隐藏。"""
    groups = get_groups()
    group = groups.get(group_name)
    if not isinstance(group, dict):
        return False
    return group.get("show_totals") is True


def set_overview_totals(group_name: str, enabled: bool) -> None:
    """设置分组是否展示合计行（多进程安全）。

    只写自己这一项：分组配置可能正被别的进程或页面改动，不能整份回写。
    """

    def _set(groups: dict) -> None:
        group = groups.setdefault(group_name, {"columns": []})
        if isinstance(group, dict):
            group["show_totals"] = bool(enabled)

    _mutate_groups(_set)


# ─── 活跃分组（本机视图状态） ────────────────────────────────


def get_active_group() -> str:
    """获取当前活跃分组名。

    这是「上次看到哪个分组」的本机视图状态，不进 profile.json：切换页签的
    频率远高于结构编辑，同步它只会制造无意义的版本。
    """
    # 老版本把它记在 interface.json 的 profile 节点里；这里先确保迁移跑过一次，
    # 否则升级后第一次读到的会是空值。
    layout.ensure_migrated()
    node = get_interface_store().get_node("ui_state", {})
    if not isinstance(node, dict):
        return ""
    value = node.get(_ACTIVE_GROUP_KEY, "")
    return value if isinstance(value, str) else ""


def set_active_group(name: str) -> None:
    """设置当前活跃分组名（多进程安全，只动 ui_state 这一项）。"""
    get_interface_store().update_node("ui_state", {_ACTIVE_GROUP_KEY: name})


# ─── 提醒历史 ────────────────────────────────────────────────


def get_alert_history() -> dict[str, str]:
    """获取提醒去重历史 {alert_key: timestamp}"""
    history = get_interface_store().get_node(_ALERT_HISTORY_KEY)
    return dict(history) if isinstance(history, dict) else {}


def set_alert_history(history: dict[str, str]) -> None:
    """整体替换提醒历史（多进程安全）"""
    get_interface_store().mutate_node(
        _ALERT_HISTORY_KEY, lambda _: dict(history))


def mark_alert(alert_key: str, timestamp: str) -> None:
    """标记一个提醒已发送（多进程安全）"""

    def _merge(old: Any):
        current = old if isinstance(old, dict) else {}
        current[alert_key] = timestamp
        return current

    get_interface_store().mutate_node(_ALERT_HISTORY_KEY, _merge)


def is_alert_marked(alert_key: str) -> bool:
    """检查提醒是否已标记过"""
    return alert_key in get_alert_history()


def unmark_alert(alert_key: str) -> None:
    """移除一个提醒标记（条件不满足时调用，允许下次重新触发）（多进程安全）"""

    def _merge(old: Any):
        current = old if isinstance(old, dict) else {}
        current.pop(alert_key, None)
        return current

    get_interface_store().mutate_node(_ALERT_HISTORY_KEY, _merge)
