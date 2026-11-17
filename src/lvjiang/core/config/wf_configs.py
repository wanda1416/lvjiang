"""全局任务参数存储 —— workflow.json 同步文档

文件结构：``{"workflow_params": {wf_id: {参数 dict}}}``，一级 key 只有
``workflow_params``，存全部工作流的全局任务参数（日常 / 批量 / 调律共享）。

该文件是账户级同步文档：core/sync/core_documents.py 登记 ``kind=workflow``、
云端 ``label=__global__``；冲突输方不留副本，按云端最新版直接覆盖。

旧存储是 session.json 顶层 ``wf_configs`` 节点，首次访问时一次性迁出并删除
旧节点（可重复执行）；没有旧节点即无事发生。
"""
from __future__ import annotations

import threading
from collections.abc import Iterable
from copy import deepcopy
from typing import Any

from loguru import logger

from ..fs_util import atomic_write_text
from ..json_files import formatted_json

# workflow.json 一级 key
_NODE_KEY = "workflow_params"
# 旧 session.json 顶层节点（迁移后删除）
_LEGACY_NODE_KEY = "wf_configs"

_WRITE_LOCK = threading.RLock()


# ─── 文档读写 ──────────────────────────────────────────────


def _path():
    from ... import constants

    return constants.WORKFLOW_CONFIG_PATH


def _read_document() -> dict[str, Any]:
    """读取整份文档；文件缺失或损坏返回空文档（迁移先于读取执行）。"""
    path = _path()
    if not path.exists():
        return {}
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        logger.error(f"全局任务参数读取失败: {path}: {error}")
        return {}
    return data if isinstance(data, dict) else {}


def _write_document(document: dict[str, Any]) -> None:
    atomic_write_text(_path(), formatted_json(document), prefix=".workflow_")


def _migrate_legacy_node() -> None:
    """session.json 的 ``wf_configs`` 节点 → workflow.json，只迁一次。"""
    if _path().exists():
        return
    from .session import get_session_store

    node = get_session_store().get_node(_LEGACY_NODE_KEY)
    if not isinstance(node, dict) or not node:
        return
    _write_document({_NODE_KEY: deepcopy(node)})
    get_session_store().delete_node(_LEGACY_NODE_KEY)
    logger.info("全局任务参数已从 session.json 迁出至 workflow.json")


def _read_params() -> dict[str, dict[str, Any]]:
    with _WRITE_LOCK:
        _migrate_legacy_node()
        node = _read_document().get(_NODE_KEY)
        return deepcopy(node) if isinstance(node, dict) else {}


def _write_params(params: dict[str, dict[str, Any]]) -> None:
    with _WRITE_LOCK:
        _write_document({_NODE_KEY: params})
        _mark_dirty()


def _mark_dirty() -> None:
    """写路径标脏：账户级 workflow 文档待同步。"""
    from ..sync.dirty import mark_dirty
    from ..sync.registry import GLOBAL_LABEL

    mark_dirty(GLOBAL_LABEL, "workflow")


# ─── 对外接口（与旧 session.json 节点存储等价） ────────────


def get_wf_config(wf_id: str) -> dict[str, Any]:
    """读取指定工作流的配置（不存在返回空 dict）

    返回独立副本，调用方修改不会影响落盘内容。
    """
    value = _read_params().get(wf_id)
    return deepcopy(value) if isinstance(value, dict) else {}


def set_wf_config(wf_id: str, config: dict[str, Any]) -> None:
    """写入指定工作流的配置（深拷贝切断调用方引用）"""
    config = deepcopy(config)
    params = _read_params()
    params[wf_id] = config
    _write_params(params)


def update_wf_config(wf_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """字段级更新指定工作流配置（原子浅合并）

    调用方只传自己负责的字段；未出现在 patch 中的字段原样保留。
    返回写入后的配置副本，便于测试或调用方继续使用快照。
    """
    patch = deepcopy(patch)
    with _WRITE_LOCK:
        params = _read_params()
        current = params.get(wf_id)
        current = current if isinstance(current, dict) else {}
        merged = {**current, **patch}
        params[wf_id] = merged
        _write_params(params)
        return deepcopy(merged)


def delete_wf_config(wf_id: str) -> None:
    """删除指定工作流配置（不存在时静默）"""
    with _WRITE_LOCK:
        params = _read_params()
        if wf_id not in params:
            return
        params.pop(wf_id)
        _write_params(params)


#: 脚本编辑器临时加载的脚本 id 前缀（见 workflows/metadata.py）；这类 id
#: 只在编辑器会话内有效，落到全局参数里就是垃圾。
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
    with _WRITE_LOCK:
        params = _read_params()
        kept: dict[str, Any] = {}
        for wf_id, value in params.items():
            if wf_id in keep and not str(wf_id).startswith(TRANSIENT_ID_PREFIX):
                kept[wf_id] = value
            else:
                removed.append(str(wf_id))
        if removed:
            _write_params(kept)
    return sorted(removed)


def get_all_wf_configs() -> dict[str, dict[str, Any]]:
    """读取全部工作流配置（返回独立副本）"""
    return _read_params()
