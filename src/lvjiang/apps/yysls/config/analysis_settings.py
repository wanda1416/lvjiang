"""按备战方案隔离的分析设置；文件位置和现有字段不变。"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from lvjiang.core.user_config import User, load_user_metadata, mutate_user_metadata

_FIELD = "graduation_analysis"


def get_graduation_analysis_settings(
    username: str, plan_id: str, users_dir: Path | None = None,
) -> dict[str, Any]:
    user = load_user_metadata(username, users_dir)
    settings = user.extra_fields.get(_FIELD) if user is not None else None
    value = settings.get(plan_id) if isinstance(settings, dict) else None
    return deepcopy(value) if isinstance(value, dict) else {}


def set_graduation_analysis_settings(
    username: str, plan_id: str, value: dict[str, Any], users_dir: Path | None = None,
) -> None:
    def update(user: User) -> None:
        settings = user.extra_fields.get(_FIELD)
        if not isinstance(settings, dict):
            settings = {}
            user.extra_fields[_FIELD] = settings
        settings[plan_id] = deepcopy(value)

    mutate_user_metadata(username, update, users_dir)
