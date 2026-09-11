"""自动调律的用户级配置。

自动调律的部位、规则和运行参数天然随用户变化，统一存放在
``users/{username}.json`` 的 ``workflow_params.auto_tuning``。旧版
``session.json.wf_configs.auto_tuning`` 不再读取，也不做迁移。
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from ....core.user_config import (
    get_user_workflow_params,
    set_user_workflow_params,
)
from .tune_slots import DEFAULT_SLOTS

WORKFLOW_ID = "auto_tuning"

_DEFAULT_CONFIG: dict[str, Any] = {
    "selected_slots": list(DEFAULT_SLOTS),
    "rules": {},
    "switches": {},
    "base_group": "default",
    "skip_tuning": False,
    "pc_background_scroll": False,
    "use_stone_cache": True,
    "initial_stone_check_enabled": False,
    "initial_stone_min_count": None,
    "validate_stone_cache": False,
    "scroll_strategy": "",
    "skip_start": None,
    "target_cell": None,
    "min_level": None,
}


def default_auto_tuning_config() -> dict[str, Any]:
    """返回全新的默认配置；所有调律规则默认不选。"""
    return deepcopy(_DEFAULT_CONFIG)


def load_user_auto_tuning_config(
    username: str,
    users_dir: Path | None = None,
) -> dict[str, Any]:
    """读取指定用户配置；未配置时直接使用新默认值。"""
    config = default_auto_tuning_config()
    if not username:
        return config
    saved = get_user_workflow_params(username, WORKFLOW_ID, users_dir)
    if saved is not None:
        config.update(saved)
    return config


def save_user_auto_tuning_config(
    username: str,
    config: dict[str, Any],
    users_dir: Path | None = None,
) -> None:
    """完整替换指定用户的自动调律配置。"""
    set_user_workflow_params(username, WORKFLOW_ID, config, users_dir)


def active_username() -> str:
    """返回 session 中当前用户的稳定用户名。"""
    from ....core.config.session import get_session_store

    value = get_session_store().get_active("user", "")
    return value if isinstance(value, str) else ""
