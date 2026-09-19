"""工作流配置统一存储 —— session.json wf_configs 节点

``{wf_id: {配置 dict}}``，日常 / 批量 / 调律三个 Tab 共享。
各 Tab 只关心自己的 wf_id，核心层零感知工作流语义。

读写委托 SessionStore（锁内原子读-改-写），与 session.json 其他
节点（ui_state / settings / daily 等）互不干扰。
"""
from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from typing import Any

from .session import get_session_store

# session.json 顶层 key
_NODE_KEY = "wf_configs"


# ─── 对外接口 ──────────────────────────────────────────────


def get_wf_config(wf_id: str) -> dict[str, Any]:
    """读取指定工作流的配置（不存在返回空 dict）

    返回独立副本，调用方修改不会影响 store 内部态。
    """
    node = get_session_store().get_node(_NODE_KEY, {})
    if not isinstance(node, dict):
        return {}
    value = node.get(wf_id)
    return deepcopy(value) if isinstance(value, dict) else {}


def set_wf_config(wf_id: str, config: dict[str, Any]) -> None:
    """写入指定工作流的配置（原子读-改-写，不影响其他 wf_id）

    深拷贝 config 以切断调用方引用，防止外部篡改 store 内部态。
    """
    config = deepcopy(config)
    get_session_store().mutate_node(
        _NODE_KEY,
        lambda old: {**(old if isinstance(old, dict) else {}), wf_id: config},
    )


def update_wf_config(wf_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """字段级更新指定工作流配置（原子浅合并）

    调用方只传自己负责的字段；未出现在 patch 中的字段原样保留。
    返回写入后的配置副本，便于测试或调用方继续使用快照。
    """
    patch = deepcopy(patch)

    def _merge(old: Any) -> dict[str, dict[str, Any]]:
        node = old if isinstance(old, dict) else {}
        current = node.get(wf_id)
        current = current if isinstance(current, dict) else {}
        merged = {**current, **patch}
        return {**node, wf_id: merged}

    node = get_session_store().mutate_node(_NODE_KEY, _merge)
    value = node.get(wf_id) if isinstance(node, dict) else {}
    return deepcopy(value) if isinstance(value, dict) else {}


def delete_wf_config(wf_id: str) -> None:
    """删除指定工作流配置（不存在时静默）"""
    get_session_store().mutate_node(
        _NODE_KEY,
        lambda old: {k: v for k, v in (old if isinstance(old, dict) else {}).items()
                     if k != wf_id},
    )


#: 脚本编辑器临时加载的脚本 id 前缀（见 workflows/metadata.py）；这类 id
#: 只在编辑器会话内有效，落到 wf_configs 里就是垃圾。
TRANSIENT_ID_PREFIX = "__loaded__:"


def prune_wf_configs(valid_ids: Iterable[str]) -> list[str]:
    """删除不属于任何现存脚本的配置项，返回被删的 id（已排序）。

    ``valid_ids`` 必须是**全部**可发现脚本的 id（``discover_scripts()``），
    而不是当前入口展示的子集——否则隐藏/专用脚本（如 auto_tuning）的配置
    会被误删。临时 id（``__loaded__:``）一律视为失效。

    只在用户编辑脚本配置时顺带调用，不在启动期做：孤儿键是多次重构改名
    留下的，攒着没有坏处，删错了才有。
    """
    keep = set(valid_ids)
    removed: list[str] = []

    def _prune(old: Any) -> dict[str, Any]:
        node = old if isinstance(old, dict) else {}
        kept: dict[str, Any] = {}
        for wf_id, value in node.items():
            if wf_id in keep and not str(wf_id).startswith(TRANSIENT_ID_PREFIX):
                kept[wf_id] = value
            else:
                removed.append(str(wf_id))
        return kept

    get_session_store().mutate_node(_NODE_KEY, _prune)
    return sorted(removed)


def get_all_wf_configs() -> dict[str, dict[str, Any]]:
    """读取全部工作流配置（返回独立副本）"""
    node = get_session_store().get_node(_NODE_KEY, {})
    return deepcopy(node) if isinstance(node, dict) else {}
