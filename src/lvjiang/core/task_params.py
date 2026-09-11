"""任务启动参数解析：元数据默认值、共享配置与用户独立覆盖。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .config.wf_configs import get_wf_config
from .user_config import get_user_workflow_params


def parameter_defaults(parameter_defs: list[dict]) -> dict[str, Any]:
    return {
        str(item["name"]): deepcopy(item.get("default"))
        for item in parameter_defs
        if isinstance(item, dict) and item.get("name") and "default" in item
    }


def _declared(values: dict[str, Any], parameter_defs: list[dict]) -> dict[str, Any]:
    names = {
        str(item["name"])
        for item in parameter_defs
        if isinstance(item, dict) and item.get("name")
    }
    return {key: deepcopy(value) for key, value in values.items() if key in names}


def resolve_task_params(
    workflow_id: str,
    username: str,
    parameter_defs: list[dict],
    users_dir: Path | None = None,
) -> tuple[dict[str, Any], str]:
    """返回最终启动变量及来源（global/user）。"""
    override = (
        get_user_workflow_params(username, workflow_id, users_dir) if username else None
    )
    return merge_task_params(parameter_defs, get_wf_config(workflow_id), override)


def merge_task_params(
    parameter_defs: list[dict],
    shared_config: dict[str, Any],
    user_override: dict[str, Any] | None,
) -> tuple[dict[str, Any], str]:
    """用调用方提供的启动快照合成最终参数。"""
    shared = _declared(shared_config, parameter_defs)
    effective = {**parameter_defaults(parameter_defs), **shared}
    if user_override is None:
        return effective, "global"
    effective.update(_declared(user_override, parameter_defs))
    return effective, "user"
